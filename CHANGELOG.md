# Changelog

All notable changes to OSTG / Netgen Traffic Generator will be documented in this file.

## [0.5.210] - 2026-08-23

**OSPFv3 no longer silently fails on freshly created FRR
containers.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23: added a new
device with BGP v4+v6 and OSPF v4+v6 all enabled up-front.
OSPFv3 (IPv6) didn't come up on FRR. Selecting the v6 row in
the OSPF table and clicking Apply worked — by the time the
second apply ran, `ospf6d` was fully initialized.

Root cause: `configure_ospf_neighbor` in `utils/ospf.py` had a
readiness retry loop that only tested `vtysh -c 'show ip ospf'`
(the v4 daemon, `ospfd`). On a freshly-created container
`ospf6d` takes longer to initialize than `ospfd`. If the vtysh
heredoc batch ran while `ospf6d` was still starting, the v6
commands (`router ospf6 …`, `interface X\n ipv6 ospf6 area …`)
were silently rejected — but vtysh returns exit_code 0 for the
whole batch based on parser success, not per-command success.
`configure_ospf_neighbor` returned True, the client saw
"success", and OSPFv3 was quietly not configured. A later
manual apply hit a fully-ready `ospf6d` and worked.

Fix:
- The readiness loop now gates on BOTH `ospfd` and `ospf6d`.
  Break only when both required daemons respond.
- `want_ipv6` peeks at `ospf_config["ipv6_enabled"]` (or the
  `ipv6` payload arg) so v4-only applies don't stall waiting
  for `ospf6d`.
- Respects `_apply_address_families` — a manual partial-apply
  scoped to `["IPv4"]` skips the ospf6d wait.
- Failure warning names both daemons so the ospf6d flavor of
  this bug is grep-able in server logs.

Files touched:
- `utils/ospf.py` — `configure_ospf_neighbor` readiness loop.

Tests: `tests/test_v05210_ospf6_daemon_readiness.py` — 6
source-level lock-ins (ospf6d probed, ospfd still probed,
want_ipv6 gate, partial-apply narrowing, break requires both
daemons, warning names both).

**Server-side fix** — netgen-server restart required on srv06
to pick up the new readiness gate. Existing devices are
unaffected (their containers are already warm). Fresh Add
Device flows will no longer skip OSPFv3.

## [0.5.209] - 2026-08-23

**Add OSPF / Add IS-IS is now additive on address families —
adding IPv6 no longer silently disables an existing IPv4
adjacency.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23 (immediately
after v0.5.208): device had IPv4 OSPF up (green Full/Backup).
Operator opened Add OSPF, unchecked IPv4 and checked IPv6
(intending "add IPv6"), clicked Add. The IPv4 row disappeared —
existing v4 got silently disabled.

Root cause: `widgets/devices_tab.py:_update_device_protocol`
merges the new config with existing via `merged_config.update
(config)`. Post-v0.5.205 the Add OSPF dialog ALWAYS emits
`ipv4_enabled` / `ipv6_enabled` from its checkboxes, so
update() overwrites the existing True with the dialog's False.
The OSPF branch preserved `area_id_ipv4/6`,
`graceful_restart_ipv4/6`, `route_pools`, `p2p_ipv4/6` — but
NOT the enable flags. Same shape in the IS-IS branch post-
v0.5.207.

Fix: additive-preserve. If `existing_config` had an AF
enabled, the merged config keeps it enabled regardless of what
the dialog said. Add only enables AFs; it never disables them.
To disable an AF, use the per-AF Delete button on that row
(v0.5.205 for OSPF, v0.5.207 for ISIS).

Files touched:
- `widgets/devices_tab.py:_update_device_protocol` — OSPF and
  IS-IS branches gain the additive-preserve pair.

Tests: `tests/test_v05209_add_protocol_additive.py` — 8 tests
(OSPF add-v6-preserves-v4 as reported; symmetric add-v4-
preserves-v6; default both checked still enables both on a
fresh device; ISIS parity in both directions; Edit-mode
omitted-flags don't disable; source-level lock-ins for both
branches).

## [0.5.208] - 2026-08-23

**OSPF table's Interface column no longer reads "Unknown" when
the adjacency is up.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23 (immediately
after the v0.5.207 upgrade): OSPF row rendered green with
`Full/Backup` — adjacency real, DB fresh — but the Interface
column said "Unknown".

Root cause: `widgets/add_ospf_dialog.py:get_values()` doesn't
emit an `interface` key (it emits area_id, graceful_restart,
router_id, hello/dead intervals, and post-v0.5.205 the AF
flags — no interface). So any ospf_config produced by Add OSPF
lacks the field. The render loop in
`utils/devices_tab_ospf.py` around line 452 computed
`ospf_interface = "Unknown"` unless `ospf_config["interface"]`
was set, then reused that single string for every AF row.
Meanwhile FRR's `show ip ospf neighbor` output **includes the
interface** (parsed at `utils/ospf.py:1105-1114` into each
neighbor dict's `interface` field), and the per-neighbor
render loop happily pulled `neighbor_id`, `state`, `priority`,
`dead_time`, `up_time` — but silently dropped
`neighbor.interface`.

Fixes:
- Rename the pre-loop compute to `ospf_interface_fallback` so
  it's obvious it's just a fallback.
- Inside the per-neighbor loop, prefer
  `neighbor.get("interface")` when a live neighbor is present.
  `ospf_interface = live_iface or ospf_interface_fallback`.
- Improved the fallback for configs that lack an `interface`
  key: derive from the device's VLAN (`vlan{N}`) or physical
  interface string (`iface.split(" - ")[1]`) — matches what
  `configure_ospf` on the server would pick. Only when nothing
  is derivable does the column read "Unknown".

Files touched:
- `utils/devices_tab_ospf.py` — `update_ospf_table` render loop.

Tests: `tests/test_v05208_ospf_interface_column.py` — 4
source-level lock-ins (per-row prefers live neighbor,
fallback exists and starts Unknown, fallback derives from
iface when config lacks key, column-4 setItem reads the
per-row variable).

**Operator-visible symptom after upgrade:** the Interface
column now shows the actual OSPF interface (`ens4np0`,
`vlan200`, etc.) whenever FRR reports an adjacency, or a
sensible derived value when the adjacency isn't yet formed.

## [0.5.207] - 2026-08-23

**Cross-protocol audit bundle — four bugs adjacent to the
v0.5.202/v0.5.203/v0.5.205 work.**

After the v0.5.205 OSPF fix, a targeted audit across BGP,
OSPF, and ISIS turned up parity gaps and one lingering
disconnect-without-reconnect. All four fixes ship together.

### 1. BGP delete no longer nukes the whole device on a
single-neighbor click (highest blast radius)

Pre-fix `utils/devices_tab_bgp.py:prompt_delete_bgp` read only
column 0 (device name) and fired `/api/bgp/cleanup` for the
entire device — so clicking Delete on any of the N per-neighbor
rows in the BGP table dropped **every** BGP session on that
router (both AFs, every peer). The BGP table shows one row per
neighbor per AF (`bgp_headers` at line 41), so this was worse
per-click than the analogous OSPF v0.5.205 case.

Fix: read column 2 (Neighbor Type: IPv4/IPv6) and column 3
(Neighbor IP). Remove ONLY that specific neighbor from the AF's
comma-separated `bgp_neighbor_ipv4` / `bgp_neighbor_ipv6`
string. `/api/bgp/cleanup` is SKIPPED (would nuke all other
peers); next Apply BGP reconciles via the v0.5.199 cleanup-
then-configure path. If the removal empties one AF's list, the
`*_enabled` flag flips off so the table + apply pipeline treat
that AF as retired. Only when BOTH lists become empty does the
handler fall through to full-device removal (fires
`/api/bgp/cleanup` like pre-fix).

### 2. ISIS delete no longer conflates the two topologies

Same shape as the OSPF v0.5.205 bug. Pre-fix
`utils/devices_tab_isis.py:prompt_delete_isis` ignored column 2
(Neighbor Type) and always tore down both topologies. ISIS
multi-topology in this codebase emits one row per AF
(`update_isis_table` around line 856-861), so a click on the
IPv6 row also dropped the IPv4 adjacency.

Fix: read column 2, per-AF `*_enabled` flip when both AFs
enabled (`/api/isis/cleanup` skipped), full-device removal only
when the last AF is being deleted.

### 3. ISIS add dialog now has Address Families checkboxes

Pre-fix `widgets/add_isis_dialog.py:get_values()` returned no
`ipv4_enabled` / `ipv6_enabled` at all, and the
`update_isis_table` fallback defaulted BOTH AFs to True
unconditionally — including for single-stack devices, which is
arguably worse than the OSPF pre-v0.5.205 behavior that at
least gated on device address presence. Result: every ISIS Add
produced two rows per device regardless of what the user
wanted, and a v4-only device got a phantom v6 row.

Fixes:
- Add `Address Families` `QGroupBox` with `Enable IPv4` /
  `Enable IPv6` checkboxes (both default checked).
- `get_values()` emits the flags in Add mode; hides the group
  and omits the flags in Edit mode (so the merge in
  `_update_device_protocol` preserves stored state).
- `_validate()` rejects zero-AF with a helpful dialog.
- Also fixed the `update_isis_table` fallback: legacy configs
  with no flags now infer from device address presence
  (parity with OSPF), rather than the dead-code `else: True`
  that flipped both AFs True regardless.

### 4. OSPF Apply → inline edits no longer silently drop

Pre-fix `utils/devices_tab_ospf.py` around line 2734 (the
Apply-time deferred-reload closure) disconnected
`ospf_table.cellChanged` with no-arg (wipes ALL slots),
`QTimer.singleShot(0, self.update_ospf_table)` — and NEVER
reconnected. After Apply OSPF, inline OSPF edits (timers, area,
graceful-restart) silently no-op'd until something else re-
wired the signal. Same class as the v0.5.202 BGP inline-edit
bug.

Fix: bundle refresh + reconnect in a single `_refresh_and_
rewire` closure that runs under `QTimer.singleShot(0, ...)` and
reconnects BOTH the DevicesTab pass-only stub AND the
`OSPFHandler.on_ospf_table_cell_changed` real write-back
handler after the refresh.

### Verified non-bugs (audit cleared)

- BGP add dialog — already emits AF flags.
- BGP monitor — 200-with-empty on container-missing correctly
  clears the DB (see v0.5.206 changelog for the trace).
- ISIS monitor — explicit `NotFound` clear at
  `utils/isis_monitor.py:180-206`.
- `_update_device_protocol` dispatch in `widgets/devices_tab
  .py:9450-9494` — all three protocol branches OK.
- Manual-override 2-min TTL — no stuck-ON path.

Files touched:
- `utils/devices_tab_bgp.py:prompt_delete_bgp`
- `utils/devices_tab_isis.py:prompt_delete_isis` +
  `update_isis_table` fallback
- `widgets/add_isis_dialog.py` (Address Families group +
  get_values + _validate)
- `utils/devices_tab_ospf.py` around line 2734 (Apply-time
  refresh_and_rewire closure)

Tests: `tests/test_v05207_protocol_audit_bundle.py` — 15 tests
(BGP per-neighbor scoping across AFs + full-cleanup on last
peer + AF flag flip; ISIS per-AF disable for both v4 and v6 +
last-AF cleanup; ISIS add-dialog default + v4-only + zero-AF
rejection + edit-mode hide; source-level lock-ins for all four
fixes). 182 total BGP/OSPF/ISIS tests pass, 0 regressions.

## [0.5.206] - 2026-08-23

**OSPF status stops lying green when the FRR container is
gone.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23: `sudo docker ps`
on srv06 showed no `device_*` container for the OSPF-configured
device, but the netgen client still displayed a green OSPF row
with `10.254.0.102 Full/Backup`, priority 128, dead-timer ~33s
counting down, uptime ~49s — a completely live-looking
adjacency that hadn't existed for minutes/hours.

Root cause: the client polls
`/api/ospf/status/database/<device_id>`, which reads the DB
snapshot written by `OSPFStatusMonitor`. Pre-fix
`_check_single_device_ospf_status` returned `None` when the
underlying `/api/ospf/status/<id>` endpoint returned 404
(container missing), and `_check_ospf_status_batch` silently
skipped the DB update on None (`if ospf_status: ... else:
logger.info(...)`). So the last-known Full/Backup snapshot
stayed frozen in the DB indefinitely, and the client faithfully
displayed it.

ISIS monitor (`utils/isis_monitor.py:180-206`) already handles
this case correctly — writes an all-down snapshot when the
container is missing. OSPF was the parity gap.

Fix: on 404, `_check_single_device_ospf_status` now returns a
synthesized "all down" status dict (`ospf_established=False`,
`ospf_state='Down'`, `neighbors=[]`, all `*_running` and
`*_established` flags False, uptimes None). The caller writes
that through to the DB via `_update_device_ospf_status`, and
the UI shows Down within one monitor cycle (~10s). Transient
5xx / network errors still return None (skip the update) so the
UI doesn't flap on brief server stumbles.

BGP monitor was checked: it already writes an all-down state
when the container is missing (its status endpoint returns 200
with an empty neighbors array rather than 404, so the existing
200-parse path produces the correct empty write). No change
needed there.

Files touched:
- `utils/ospf_monitor.py:_check_single_device_ospf_status` —
  404 branch now synthesizes an all-down status dict.

Tests: `tests/test_v05206_ospf_monitor_clear_stale.py` — 5
tests (404 returns synthesized down not None, 404 flows
through to DB clear, non-404-non-200 still returns None, 200
with real status passes through unchanged, source-level
lock-in).

**Operator note:** After upgrading the server on srv06 and
restarting, the stale OSPF row will clear on the next monitor
tick (~10s). No client-side change required for this fix — the
client just reads whatever the DB says.

## [0.5.205] - 2026-08-23

**Add OSPF now honors the AF the user picked; Delete OSPF is
row-scoped instead of nuking the whole device.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23: selected device1
(has both v4 + v6 addresses), clicked Add OSPF, filled the form,
hit Add. Two rows appeared in the OSPF table — an IPv4 row with
a real `Full/Backup` neighbor and an IPv6 row that said "No
Neighbors" (because OSPFv3 was never actually configured on the
router). Then clicking Delete on the v6 row also killed the v4
row and lost the working neighbor.

Root causes:

1. **`AddOspfDialog.get_values()` never emitted
   `ipv4_enabled`/`ipv6_enabled`.** The OSPF table's fallback in
   `utils/devices_tab_ospf.py:475-482` filled the missing keys
   from `bool(device_ipv4)` / `bool(device_ipv6)` — so any
   device with both addresses got two rows regardless of what
   the user wanted.
2. **`prompt_delete_ospf` read only column 0 (device name).**
   Column 3 (Neighbor Type — IPv4/IPv6) was ignored, and the
   handler unconditionally fired `POST /api/ospf/cleanup` for
   the whole device, wiping BOTH AFs.

Fixes:

- `widgets/add_ospf_dialog.py`
  - New "Address Families" `QGroupBox` with `Enable IPv4` and
    `Enable IPv6` checkboxes (both default checked).
  - `get_values()` returns `ipv4_enabled` and `ipv6_enabled` in
    Add mode. In Edit mode the group is hidden and the two keys
    are omitted so the merge in `_update_device_protocol`
    doesn't clobber the caller's stored flags.
  - `_validate()` rejects "neither AF checked" with a helpful
    dialog — a zero-AF OSPF config never renders and Apply is a
    silent no-op, which would just look broken.
- `utils/devices_tab_ospf.py:prompt_delete_ospf`
  - Reads `protocol_type` from row column 3.
  - If both AFs are enabled and the operator clicks one row:
    flip only that AF's `*_enabled` flag off, save session,
    refresh table. The `POST /api/ospf/cleanup` call is
    intentionally SKIPPED (whole-device cleanup would drop the
    surviving AF's peer too); the operator's next Apply OSPF
    Configuration reconciles via the v0.5.199 cleanup-then-
    configure path.
  - If only one AF is enabled (this row is the last one):
    full-removal path — same shape as pre-fix, fires
    `/api/ospf/cleanup`.

Tests: `tests/test_v05205_ospf_af_scoped.py` — 11 tests
(dialog default both, v4-only, v6-only, zero-AF rejection,
edit-mode-hides-group; per-AF disable for v4 and v6 rows,
last-AF fires full cleanup, cancel is a no-op; source-level
lock-ins for `get_values` and `prompt_delete_ospf`).

## [0.5.204] - 2026-08-23

**Paid licenses stop getting silently invalidated on every VPN
toggle / WiFi/Ethernet switch.**

Operator report on JNPR-MAC-HWXVX1 2026-08-23: activated a paid
JWT, worked once, then every subsequent client restart dropped
straight back to the activation screen even though
`~/.netgen/license.jwt` was still on disk with a valid RS256
signature. Root cause: `machine_fingerprint()` mixed in
`uuid.getnode()`, which returns whichever NIC's MAC Python
happens to sample at import time. On macOS/Linux with WiFi +
Ethernet + VPN utun* the "sampled" NIC rotates whenever the
active interface changes, so the fingerprint the JWT was bound
to (`7c1e1671…`) stopped matching what the client computed
(`54f7e766…`) and verify_jwt returned `license is bound to a
different machine`. From the operator's perspective the license
just "gets lost every restart".

Fixes:

1. **Drop `_stable_mac()` from `machine_fingerprint()` inputs.**
   The stable inputs are now `socket.gethostname()`,
   `platform.node()`, `platform.machine()`, and the persistent
   `~/.netgen/fingerprint.salt` (16 random bytes generated on
   first launch). Salt was already load-bearing for uniqueness
   across installs — dropping the MAC doesn't cost anything on
   that front, and the salt survives reboots, package reinstalls,
   VPN toggles, and NIC swaps.
2. **Add `_legacy_machine_fingerprint()`.** Preserves the exact
   old MAC-inclusive algorithm — verify_jwt accepts EITHER, so
   paid JWTs minted against the old algorithm keep working while
   the operator re-issues against the new stable fingerprint.
   Two-rung migration; no hard cut-over.
3. Server-side `device_fingerprint_hash` validation is a
   64-char hex regex only (tlink-license-server
   `users.js:359`) — the content is entirely client-defined,
   so this change is compatible with existing mint code.

Files touched:
- `utils/license.py` — `machine_fingerprint()` (drop MAC),
  new `_legacy_machine_fingerprint()`, `verify_jwt` fingerprint
  check accepts both.

Tests: `tests/test_v05204_stable_fingerprint.py` — 6 tests
(stability under `_stable_mac` drift, legacy still MAC-varying,
salt still load-bearing, verify_jwt accepts new AND legacy AND
rejects random).

**Operator note:** After upgrading, get the new stable
fingerprint with `venv/bin/python -c "from utils import license;
print(license.machine_fingerprint())"` and re-issue the paid JWT
against it via tlink-license-server. Until you do, the current
JWT still verifies as long as your current MAC matches the one
captured when it was minted (that's what the legacy accept is
for).

## [0.5.203] - 2026-08-23

**Add OSPF / Add ISIS now show up in the protocol table
immediately (client-side).**

Operator report: selected a device, clicked Add OSPF, filled
the dialog, clicked Add — nothing appeared in the OSPF table.
Same shape for ISIS.

Root cause — [widgets/devices_tab.py:9467](widgets/devices_tab.py:9467)
in `_update_device_protocol`:

```python
elif protocol == "OSPF":
    ...
    pass          # ← did nothing. Table never refreshed.
elif protocol == "IS-IS" or protocol == "ISIS":
    ...
    pass          # ← same story.
```

Both branches were literally `pass`, with a comment claiming
the table would "refresh on the next periodic update or when
Apply is clicked". Result: the added `ospf_config` /
`isis_config` sat in `device_info` waiting for a periodic tick
(30–60 s away, sometimes never), and the operator saw a dead
UI and assumed the Add had failed.

**Fix** — mirror the BGP branch that got fixed in v0.5.202:
disconnect the `cellChanged` signal, call
`update_ospf_table()` / `update_isis_table()` to rebuild
immediately, then reconnect BOTH the DevicesTab-level stub
AND the real handler in `OSPFHandler`/`ISISHandler`. The
double-reconnect is what keeps inline edits (area-id,
neighbor timers, redistribute settings) persisting after the
rebuild — same class of disconnect-race the BGP fix closed.

**Files:**
- `widgets/devices_tab.py` — OSPF + ISIS branches of the
  `_update_device_protocol` refresh-per-protocol dispatch
  each get the disconnect / refresh / reconnect-both pattern.
- `tests/test_v05203_ospf_isis_add_refresh.py` — 5 source-
  level lock-ins: OSPF+ISIS branches call their refresh, both
  reconnect stub + real handler, and v0.5.202's BGP fix is
  still in place.

## [0.5.202] - 2026-08-23

**BGP row inline edits (hold-time, keepalive, source, neighbor,
ASN) now actually persist to bgp_config again.**

Operator report: modified `bgp_hold_time` in the BGP table's
inline column 11, clicked Apply — value reverted to default 90.
Confirmed on srv06 that every Apply payload was still carrying
`bgp_hold_time='90'` even after the operator typed a new
value; the edit never made it into `device_info["bgp_config"]`.

Root cause — [widgets/devices_tab.py:9452](widgets/devices_tab.py:9452)
in `update_protocol`:

```python
self.bgp_table.cellChanged.disconnect()   # ← wipes ALL slots
self.update_bgp_table()
self.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)
# ↑ only re-wires the pass-only STUB in DevicesTab
```

`disconnect()` with no args disconnects every slot bound to
`cellChanged`, including the REAL edit handler that
`BGPHandler.__init__` wires at
`utils/devices_tab_bgp.py:54`. Only the DevicesTab-level stub
(`def on_bgp_table_cell_changed(self, row, col): pass`) got
reconnected afterwards. Once `update_protocol` ran once (which
happens on any protocol-state event → very often), inline
edits fired only into the stub and were silently dropped.
The apply payload then re-serialized the untouched
bgp_config and shipped the defaults.

**Fix**: reconnect BOTH the stub AND the real handler after
`disconnect()`. The stub is a no-op; the real handler in
BGPHandler writes the edited value back into
`device_info["bgp_config"]`.

**Files:**
- `widgets/devices_tab.py` — the protocol-update reconnect now
  wires both `self.on_bgp_table_cell_changed` (stub) and
  `self.bgp_handler.on_bgp_table_cell_changed` (real work).
- `tests/test_v05202_bgp_inline_edit_persist.py` — 3 source-
  level lock-in tests: both reconnects present, the real
  handler still contains the hold-time persist-write, and the
  DevicesTab-level stub remains a `pass` so nobody accidentally
  moves logic into the wrong side.

## [0.5.201] - 2026-08-23

**BGP partial-apply no longer wipes neighbors in the address
family the operator wasn't editing.**

Operator report on san-hp-srv06: existing IPv4 BGP session
(peer 192.168.0.1 Established). Opened the GUI, added an IPv6
BGP row, hit Apply — the v4 session went down. Client did the
right thing (partial-apply payload with `_apply_address
_families = ['ipv6']`), and the server-side DB merge also
did the right thing. But two FRR-facing paths didn't respect
the partial-apply scope.

**Bug 1** — the diff-reconfig block at [run_tgen_server.py
:9335](run_tgen_server.py:9335) read `bgp_config.get(
"bgp_neighbor_ipv4")` straight from the raw payload. A v6-
only apply doesn't carry the v4 neighbor field (client sends
only the row it was editing), so `new_ipv4_list = []`. The
diff then computed `ipv4_to_remove = [all existing v4
neighbors]` and issued `no neighbor 192.168.0.1 activate` +
`no neighbor 192.168.0.1` to FRR. Session gone.

**Fix**: guard both diff blocks (v4 + v6) with
`is_partial_apply and "<af>" not in apply_address_families`
so the diff is skipped entirely when the address family isn't
in scope.

**Bug 2** — `configure_bgp_for_device(device_id, bgp_config,
...)` at line 9572 was called with the raw payload too. On a
v6-only apply the payload has `ipv4_enabled=False` and
`bgp_neighbor_ipv4=""`, so `configure_bgp_for_device` would
independently reason "no v4 config" and tear down the v4
neighbor from its side.

**Fix**: on partial-apply, hand it a MERGED config —
`existing_bgp_config.copy(); .update(bgp_config); ` then
overlay the calculated (existing-preserving) `ipv4_enabled` /
`ipv6_enabled` flags and preserve the neighbor / update-
source fields for the out-of-scope address family.

**Files:**
- `run_tgen_server.py` — v4-diff + v6-diff guarded by
  partial-apply scope; `_bgp_config_for_frr` merged view
  handed to `configure_bgp_for_device` on partial applies.
- `tests/test_v05201_bgp_partial_apply.py` — 5 source-level
  lock-in tests covering both diff guards, the merged-config
  passthrough, and the calculated-flag override.

**Verified live on srv06**: existing v4 session survives an
IPv6-add partial apply (previously wiped immediately).

## [0.5.200] - 2026-08-23

**Audit follow-up: OSPF & ISIS get parity with the BGP fixes
from v0.5.198–v0.5.199, and two P1 filter/cleanup bugs that
made BGP route advertisement silently over-permissive get closed.**

Motivated by the operator's "what other bugs does BGP have"
audit after the four-fix v0.5.196–v0.5.199 batch. Four scans
found four issues — all shipped in v0.5.200.

**P0 — OSPF & ISIS mirror the BGP VRF-suffix bug.**
`configure_ospf_route_advertisement` / `configure_isis
_route_advertisement` emitted `ip route X null0` without the
per-device VRF suffix. Same shape as the BGP bug fixed in
v0.5.198: static routes landed in the default routing table,
but `router ospf` / `router isis CORE` inside a per-device
VRF searched only its own VRF's table for redistribute-static
→ nothing advertised. Symmetric fix — compute
`_vrf_route_suffix` from `FRRDockerManager.vrf_name_for_device`
and append to every `ip route` / `ipv6 route`. Cleanup paths
get the matching suffix so removal doesn't leak.

**P1 — `route-map RM-EXPORT permit 20` catch-all defeated the
prefix-list filter.**

Configure emitted:
```
route-map RM-EXPORT permit 10
 match ip address prefix-list PL-EXPORT
route-map RM-EXPORT permit 20   ← no match = permit ALL
```

The second sequence had no match clause, so it permitted
everything — anything in FRR's redistribute-static input got
advertised regardless of `PL-EXPORT`. Fix: delete the `permit
20` (implicit deny handles non-matches correctly) and prefix
each apply with `no route-map RM-EXPORT` so a stale sequence
from a pre-v0.5.200 install gets wiped on the next apply.
Same for `RM-EXPORT-IPV6`.

**Behavioural note**: before v0.5.200, the connected `network
<subnet>/N` statement was also advertised through the catch-
all → operators counted `PfxSnt = N pool + 1 connected`. After
v0.5.200, only the pool prefixes go out. If you want the
connected too, define it as a pool.

**P1 — cleanup's prefix-list seq range was hardcoded 5–50.**
Configure generates `seq += 5` per route with no cap: a pool
with 20 routes lands at seq 100, 50 routes at seq 250. The
old cleanup's `for seq in range(5, 55, 5)` loop left every
seq past 50 as an orphan. On the next apply the fresh entries
overwrote seq 5–50 but the orphan tail persisted forever.
Fix: single wildcard `no ip prefix-list PL-EXPORT` (drops the
whole list); configure recreates only the sequences it needs.

**Live verification on san-hp-srv06:**

```
before v0.5.200 (with catch-all):     PfxSnt = 21  (20 pool + 1 connected via catch-all)
after v0.5.200:                        PfxSnt = 20  (20 pool only — connected filtered)
route-map RM-EXPORT: only permit 10 (no permit 20 catch-all)
prefix-list PL-EXPORT: 20 entries, all 6.6.x, no orphan tail
```

**Files:**
- `run_tgen_server.py` — `configure_ospf_route_advertisement`,
  `configure_isis_route_advertisement`, `cleanup_ospf_route
  _advertisement`, `cleanup_isis_route_advertisement` all get
  the `_vrf_route_suffix` computation + append it to every
  `ip route` / `ipv6 route` / `no ip route` / `no ipv6 route`
  command. `configure_bgp_route_advertisement` drops the
  `permit 20` catch-all + prefixes with `no route-map` for a
  clean rebuild. `cleanup_bgp_route_advertisement` replaces
  the hardcoded seq loop with a single wildcard drop.
- `tests/test_v05200_ospf_isis_parity.py` — 7 source-level
  lock-in tests: OSPF + ISIS advertise + cleanup VRF-suffix,
  BGP catch-all removal, BGP `no route-map` prefix, cleanup
  wildcard drop.

## [0.5.199] - 2026-08-23

**BGP route-pool changes now withdraw old prefixes from the peer.**

Operator report follow-up to v0.5.198 on san-hp-srv06: attached
[p2, p5] → applied (peer got 9 prefixes ✓), then swapped to
[p6] only → applied. Peer's `show bgp summary` still counted
30 prefixes received — the old 5 (p2) + 4 (p5) never got
withdrawn even though FRR's prefix-list and config looked
correct. The routes sat in FRR's BGP RIB and kept being
re-advertised.

Two coupled bugs, both fixed:

**Bug 1 — cleanup only ran when attach list went empty.**

`configure_bgp` had two branches for its pool loop: empty
attach → run cleanup; non-empty attach → run configure. There
was no "attach changed" path — swapping `[p2,p5] → [p6]` fell
straight into configure and just added p6 on top. Fix: wrap
cleanup + configure into a single `_cleanup_then_configure`
worker so every apply for a neighbor starts by wiping any
prior route-pool state (cleanup iterates the full `all_pools
_db` so it removes stale routes from ANY prior pool, not just
the currently-attached set).

**Bug 2 — cleanup's vtysh here-doc was single-command.**

`cleanup_bgp_route_advertisement` shipped its 49 vtysh commands
as a single `docker exec … vtysh -c "<multi-line-string>"`.
vtysh's `-c` treats its argument as ONE command, so after the
first `configure terminal` succeeded (exit 0), every `no ip
route …` / `no ip prefix-list …` after it was silently ignored.
Operator saw `Command exit code: 0` + `✅ All cleanup commands
executed successfully` while FRR still had every stale route.

Fix: match the pattern
`configure_bgp_route_advertisement` already uses reliably —
`bash -c "vtysh << EOF ... EOF"` (a real shell heredoc; vtysh
reads each command from stdin line by line). Same behaviour
as the manual `docker exec … vtysh -c "cmd1" -c "cmd2"` an
operator would type by hand.

**Live verification on srv06** (same operator payload,
[p2,p5] → [p6] swap):

Before v0.5.199:
```
static routes in VRF: 29 (2.2.x + 5.5.x + p6)
FRR BGP RIB:          30
PfxSnt to peer:       30
```

After v0.5.199:
```
static routes in VRF: 20 (only 6.6.x — p6)
FRR BGP RIB:          21 (p6 + connected)
PfxSnt to peer:       21
```

**Files:**
- `run_tgen_server.py` — `configure_bgp` known-pools branch
  now dispatches `_cleanup_then_configure` (cleanup wrapped in
  try/except so a cleanup failure doesn't block the configure
  step); `cleanup_bgp_route_advertisement` switched from
  `docker exec … vtysh -c "<here-doc>"` to
  `container.exec_run(["bash","-c", "vtysh << 'EOF' … EOF"])`
- `tests/test_v05199_cleanup_before_configure.py` — 4 source-
  level lock-in tests: `_cleanup_then_configure` present +
  ordered before configure, try/except around cleanup so
  configure still runs, cleanup iterates `all_pools_db`, VRF-
  suffix guard.

## [0.5.198] - 2026-08-23

**BGP route-pool attachment now works end-to-end without a
separate "Save pools" step.** Fixes the operator-reported gap on
san-hp-srv06: attach pools p2 (5 prefixes) + p5 (4 prefixes),
click Apply — switch received all 9 prefixes on the peer's next
update (previously: 0 received).

Two coupled bugs fixed together — the v0.5.197 fix surfaced them
but did not close the loop.

**Bug 1 — server threw away the client's pool definitions.**

The Add-Device / Edit-BGP dialog already includes every pool
definition in the request body under `all_route_pools: [{name,
subnet, count, first_host, last_host, increment_type}, ...]`.
The server's `configure_bgp` was ignoring that field and
consulting only its own DB, so any workflow that skipped a
separate `Save to Database` step in the Manage Route Pools
dialog ended up with attach names referencing pools the server
had never heard of. v0.5.197 surfaced this with a WARNING; v0.5
.198 closes it by iterating `all_route_pools` and calling
`add_route_pool()` for each row (which delegates to
`update_route_pool` on duplicate names, so it's idempotent and
safe to run every Apply).

**Bug 2 — static routes landed in the wrong VRF.**

`configure_bgp_route_advertisement` emits `ip route X null0` to
create blackhole prefixes that `redistribute static` in the
BGP-per-VRF instance is supposed to advertise. But the commands
were unscoped — they landed in the default routing table, while
`router bgp <asn> vrf <name>` searches only its own VRF's table.
Result: the routes existed but never made it into BGP's RIB
(`show bgp vrf <name> summary` showed `PfxSnt: 1` — just the
connected). Fix: suffix each `ip route` and `ipv6 route`
command with ` vrf <name>` when the device has a per-device VRF
wired up (matches the same VRF resolution `_bgp_router_clause`
uses). Cleanup path gets the matching suffix so removal doesn't
leak.

**Live verification on san-hp-srv06:**

Before v0.5.198:
```
show bgp vrf vrf-5bd1df3a1f5 ipv4 unicast
 *> 192.168.0.0/24   0.0.0.0   0   32768 i    ← 1 prefix (connected only)
PfxSnt: 1
```

After v0.5.198 (same operator payload, no separate save step):
```
show bgp vrf vrf-5bd1df3a1f5 ipv4 unicast
 *> 2.2.2.0/24  ...  ?     ← from pool p2
 *> 2.2.3.0/24  ...  ?
 ... (5 rows for p2)
 *> 5.5.5.0/24  ...  ?     ← from pool p5
 ... (4 rows for p5)
 *> 192.168.0.0/24 ... i
Displayed 10 routes and 10 total paths
PfxSnt: 10
```

**Files:**
- `run_tgen_server.py` — `configure_bgp` iterates
  `all_route_pools` from payload and calls `add_route_pool()` for
  each (field-name translation: `count → route_count`, `first
  _host → first_host_ip`, `last_host → last_host_ip`);
  `configure_bgp_route_advertisement` + `cleanup_bgp_route
  _advertisement` both compute a `_vrf_route_suffix` from
  `FRRDockerManager.vrf_name_for_device()` and append it to
  every `ip route` / `ipv6 route` (and `no ip route` etc.)
  command.
- `tests/test_v05198_pool_autopersist.py` — 7 source-level
  lock-in tests: `all_route_pools` extraction, `add_route_pool`
  wiring, field-name translation, idempotency, and both VRF-
  suffix guards (advertise + cleanup).

## [0.5.197] - 2026-08-23

**BGP Apply no longer silently drops route-pool attachments when
the pool doesn't exist on the server.**

Operator report on san-hp-srv06: attached two IPv4 route pools
(`p2`, `p5`) to a BGP neighbor via the GUI, hit Apply, saw the
peer come up but zero prefixes received. The pools were saved
in `bgp_config.route_pools` but were never POSTed to
`/api/bgp/pools`, so the server's pool table was empty when
`configure_bgp()` ran.

Root cause — [run_tgen_server.py:9623](run_tgen_server.py:9623)
had a single-line gate:

    if attached_pools and all_pools:
        # advertise
    else:
        # SILENTLY cleanup — no log, no toast, no error
        _cleanup_routes(...)

When `attached_pools=['p2','p5']` (truthy) and `all_pools=[]`
(empty), the code hit the else branch and silently removed any
route advertisement. There was no signal to the operator that
the attachment names were unknown.

Fix — priority ladder replaces the silent gate:

  * Split `attached_pools` into `known` (exists in pool table)
    and `unknown` (attached name is unknown).
  * For `unknown`: log a WARNING with the pool names + append
    to `apply_warnings` list. Payload includes
    `{code: "unknown_pools", neighbor, unknown_pools, message}`.
  * For `known`: advertise them (partial success beats silent
    no-op — one working pool + one missing beats "silently drop
    all of them").
  * The response now returns `"warnings": [...]`; the client's
    sync BGP-apply picks the list up and stashes it on
    `device_info["_apply_warnings"]`; the Devices tab's Apply
    Results dialog folds them into a `⚠ Applied with warnings`
    line so the operator sees `attached pool 'p2' does not
    exist on the server — save it in Manage Route Pools first`
    without opening the log file.
  * The BGP-start restore path
    ([run_tgen_server.py:10308](run_tgen_server.py:10308)) had
    the same shape and gets the same split-and-warn treatment
    (log-only; not called from a client-visible response).

**Files:**
- `run_tgen_server.py` — `configure_bgp` split-and-warn + new
  `warnings` field in the 200 response; BGP-start restore path
  logs a WARNING for unknown attached pools instead of silently
  skipping the whole restore
- `utils/devices_tab_bgp.py` — sync BGP apply parses
  `response.json().warnings` and stashes on device_info
- `widgets/devices_tab.py` — Apply Results renders
  `_apply_warnings` as a `⚠ Applied with warnings:` line
- `tests/test_v05197_bgp_pool_warnings.py` — 6 tests: silent-
  gate absence (source-level lock-in), split-and-warn contract,
  response shape, BGP-start restore parity, client stash +
  Apply Results surfacing

## [0.5.196] - 2026-07-21

**Default self-service trial extended from 30 → 60 days.**

Operator asked for a way to activate the client for 60 days.
After weighing four shapes (mint a paid 60-day JWT via
tlink-license-server, ship a client-side master code that
unlocks an extended trial, add an in-repo `netgen-cli license
mint`, or just raise the default trial), the simplest option
won: everyone who clicks "Start trial" now gets 60 days on
first use instead of 30. The trial-used marker still enforces
one-per-install; paid JWT flow is unchanged.

- `utils/license.py` — `TRIAL_DAYS = 30 → 60`; the "trial
  ended" note now interpolates the constant instead of
  hardcoding 30.
- `netgen_cli.py` — `license trial --help` no longer says
  "30-day".
- `docs/index.html` — refreshed the "What's new in 0.5"
  section for the 0.5.x feature wave (RDMA, RFC 2544, one-way
  latency, licensing, DPDK setup wizard, DPDK orphan reap,
  server tarball, evolved admin console, Tools → Clear All
  Devices, ARP + BGP status chip fixes). Added feature cards
  for RDMA / License / Bulk cleanup, and the server tarball
  as the 5th release artifact. 30-day mentions → 60-day.
- `tests/test_v05196_trial_60_days.py` — locks in the new
  constant, verifies `start_trial` writes ~60d out, and grep-
  guards the three user-facing modules against a future
  regression to hardcoded "30-day" strings.

Existing v0.5.183 (61 tests) and v0.5.195 (7 tests) suites
still pass unchanged.

## [0.5.195] - 2026-07-20

**Client restart no longer bounces trial users to the activation
dialog when a stale `license.jwt` sits alongside a live trial.**

Operator report: "even though license is active for 30 days
trial, after restart it is again asking license activation and
does not start the app."

Root cause — [utils/license.py:604-610](utils/license.py:604)
returned `_maybe_grace(verify_jwt(token))` verbatim the moment
`~/.netgen/license.jwt` existed. If the JWT couldn't
authorise (past-grace expiry, tampered signature, bound to a
different device fingerprint), `load()` returned the invalid
License and the caller (`is_activated()`) rejected the session.
The perfectly-valid `~/.netgen/trial.json` living next to it
was never consulted. Once a paid activation existed on a
machine, any later breakage of that JWT permanently shadowed
the trial.

Fix — `load()` now:
  1. verifies the JWT (if present) → returns it iff `is_valid`
  2. otherwise reads the trial file → returns it iff `is_valid`
  3. only when both are unusable does it surface a reason
     (JWT's — more actionable than "trial expired" — if a JWT
     existed at all, else the trial's, else `no license`)

**Files:**
- `utils/license.py` — `load()` rewritten as a priority ladder
  (valid JWT > valid trial > invalid JWT > expired trial > no
  license)
- `tests/test_v05195_license_trial_fallback.py` — 7 tests
  covering every rung of the ladder, including the two forms
  of "invalid JWT shadows valid trial" (past entitlement,
  tampered signature).

## [0.5.194] - 2026-07-20

**Tools → Clear All Devices on Server — bulk cleanup path.**

When the client restarts, the server-side FRR containers, VRFs,
and device rows persist. If the client's transient view of
per-device state gets out of sync (or an operator wants a
"reset this server" button after a lab reshuffle), the fix
before this release was to right-click each row and Delete —
tedious past a handful of devices.

- **New endpoint** `POST /api/devices/clear_all` (admin-role):
  loops `device_db.get_all_devices()` and re-enters
  `/api/device/remove` via `app.test_client()` for each. Reuses
  the exact production removal path — container stop, VXLAN
  teardown, DHCP cancel, OSPF cleanup, DB purge, IP-mapping
  cleanup. Returns `{total, removed, failed, results}` with
  per-device status codes + response bodies so partial failures
  are debuggable.
- **New client action** `Tools → Clear All Devices on Server…`
  with a typed-CLEAR confirmation (destructive; the count in
  the confirmation is fetched fresh from
  `/api/device/database/devices`), success/partial toast, and
  automatic Devices-tab refresh on completion.
- **Tests** `tests/test_v05194_clear_all_devices.py` — empty
  DB, multi-device dispatch through the shared handler, and a
  lock-in test that the URL rule + `@require_role("admin")`
  don't get silently downgraded by a rename.

## [0.5.193] - 2026-07-20

**Device status chips no longer stuck yellow for multi-device / VRF
deployments and IPv4-only dual-stack configs.**

Three independent bugs in the status monitors, all surfacing as the
same UX symptom (ARP/BGP chip stays yellow even when routing works).
Verified live on san-ft-ai-srv01 with a VLAN-200 BGP session: before
the fix `arp_status=Failed, arp_ipv4_resolved=0, bgp_established=0`
despite `bgp_ipv4_state=Established`; after: `arp_status=Resolved,
arp_ipv4_resolved=1, bgp_established=1`.

**Bug 1 — `get_device_arp_status` skipped `ip vrf exec` on self-ping.**
Old code deliberately unwrapped the VRF prefix when pinging the
device's own IP, based on a comment claiming self-ping would loop
across VRFs and fail. That's false: each Linux VRF has its own
`local` table, so the bare `ping 192.168.0.2` in the default netns
returns `Network is unreachable` when the address only lives inside
the VRF. Verified on srv01: `ping 192.168.200.2` fails 100%,
`ip vrf exec vrf-c2d ping 192.168.200.2` succeeds in 0.024 ms. Fix
wraps every ARP-probe ping in the device's VRF context.

**Bug 2 — `requires_ipv6` was inferred from protocol dual-stack
flags.** Netgen's Add-Device dialog defaults BGP / OSPF / ISIS to
dual-stack (`ipv4_enabled=True, ipv6_enabled=True`) even when the
operator only fills in an IPv4 address. `get_device_arp_status`
then set `requires_ipv6=True` from those flags and demanded an
IPv6 probe that could never succeed (no target). Overall ARP
status became `Failed` forever, chip yellow. Fix: require IPv6
only when `ipv6_address` or `ipv6_gateway` is actually set —
protocol flags without a matching address are treated as
misconfiguration and ignored for status purposes.

**Bug 3 — `bgp_established` never landed in the `devices` table.**
The column exists in the schema (`utils/device_database.py` line
296), but `utils/bgp_monitor.py._update_device_bgp_status` skipped
it based on a wrong comment ("column doesn't exist"). Similarly
`utils/device_database.py update_device`'s field-mapping had the
same key commented out. Consequence: the top-level BGP rollup that
`get_all_devices` reads was pinned at False forever, driving the
BGP chip yellow even when IPv4 was Established. Fix: uncomment
both spots and correct the misleading comments.

**Files touched:**
- `run_tgen_server.py` — `get_device_arp_status` self-ping VRF
  wrap + `requires_ipv6` gate
- `utils/bgp_monitor.py` — write `bgp_established` and
  `last_bgp_check` to devices table (already-correct sub-flags
  kept)
- `utils/device_database.py` — allow `bgp_established` through
  `update_device` field-mapping
- `tests/test_v05193_arp_status.py` — 4 regression tests: VRF
  self-ping wrap, IPv4-only dual-stack no-longer-yellow,
  bgp_established in update_device field-mapping, bgp_monitor
  writes bgp_established

**Rolled up from 0.5.191/0.5.192 (never tagged, verified via
wheel deploys on srv01):**

- `resources/dpdk/install_dpdk.sh` — Step 1 disk-space check
  died silently under `set -euo pipefail` because
  `df | tail -1 | awk | sed` sent SIGPIPE to df, pipefail
  caught it, and set -e killed the script before any warning
  reached the log. Fixed with `|| true` on the pipeline and
  `local avail=""` pre-init.
- `resources/dpdk/install_dpdk.sh` — split apt install into
  required + optional. Required deps are all-or-fail as before;
  optional AF_XDP deps (`libbpf-dev`, `libxdp-dev`) are now
  best-effort — a missing package emits a warning instead of
  failing the whole install. Ubuntu 22.04 lab boxes without
  `universe` enabled were losing the entire DPDK install to a
  single "unable to locate package libxdp-dev" line.
- `resources/dpdk/install_dpdk.sh` — replaced a backtick-in-
  double-quoted-string that ran `elftools` as a shell command
  during the critical-dep error path.

## [0.5.190] - 2026-07-19

**DPDK build no longer explodes at `pmdinfogen` on x86_64 Ubuntu 22.04
— NXP DPAA/fslmc ARM-only drivers now disabled at meson configure.**

Operator hit this on san-ft-ai-srv01 during Make DPDK Ready:

```
subprocess.CalledProcessError: Command '['meson', 'runpython',
'../buildtools/pmdinfogen.py', 'elf',
'/opt/dpdk/build/drivers/libtmp_rte_bus_fslmc.a.p/bus_fslmc_fslmc_bus.c.o',
…]' returned non-zero exit status 2.
ERROR: Unhandled python exception
    This is a Meson bug and should be reported!
ninja: build stopped: subcommand failed.
```

Root cause: `pmdinfogen.py` (part of the DPDK build) uses `pyelftools`
to parse driver `.o` files. On Ubuntu 22.04's stock `python3-pyelftools`
0.27 + DPDK 23.11, parsing the fslmc bus driver `.o` files crashes
with an unhandled exception. fslmc is NXP's DPAA2/QorIQ **ARM SoC**
bus — building it on x86_64 makes zero sense in the first place, and
the crash is 100% reproducible on this platform combo.

Fix in
[resources/dpdk/install_dpdk.sh](resources/dpdk/install_dpdk.sh):
extend `meson_disable` from `net/mana` to also cover the full
NXP DPAA family:

```
net/mana,
bus/fslmc, bus/dpaa,
net/dpaa2, net/dpaa,
mempool/dpaa2, mempool/dpaa,
event/dpaa2, event/dpaa,
crypto/dpaa2_sec, crypto/dpaa_sec,
raw/dpaa2_qdma, raw/dpaa2_cmdif
```

Explicit driver list (instead of just `bus/fslmc,bus/dpaa` and
letting meson auto-drop dependents) is safer against future meson
dependency-resolution changes.

## [0.5.189] - 2026-07-19

**INSTALL.md audit — 4 factual errors fixed, 5 troubleshooting rows added,
license activation + advanced/workarounds sections added.**

Driven by an operator install session on 2026-07-19 where the guide's
claims diverged sharply from reality and left the operator stuck for
hours. Audit of everything the operator hit + everything they would
have hit if they'd followed the docs literally:

### Fixed (factual errors)

* [INSTALL.md](INSTALL.md) Path A no longer claims the client bundles a
  tarball — the client bundles only a wheel + `install_ostg_complete.py`.
* Path A's SFTP description now names what actually gets sent (wheel
  + installer, into `/tmp/netgen_install/`), not the imaginary
  "bundled tarball assets".
* Path B's `wget` URL corrected to the versioned release-download path
  (previous form 404s), and now warns that tags v0.5.22–v0.5.186 have
  no tarball on their release page (the auto-build was disabled during
  that window; only v0.5.187+ have it back).
* Every "Add TGen Chassis" renamed to the current "Add Server" menu
  label.

### Added (topics we hit today)

* **License activation** section — first-launch flow, trial path,
  status pill legend, `netgen-cli license` recipes, `NETGEN_LICENSE_*`
  envs. Was completely missing.
* Troubleshooting rows for: health=degraded/tx_worker missing (srv01's
  exact case), `ModuleNotFoundError: certifi` with the manual unblock,
  fresh-install-runs-old-installer client-SFTP overwrite gotcha,
  `/api/interfaces` all-nulls, gated menu items greyed out.
* **Advanced / workarounds** section: force a specific installer via
  raw.githubusercontent.com, backfill a tarball for an existing tag
  via `gh workflow run build-server-tarball.yml`, headless license
  activation.

## [0.5.188] - 2026-07-19

**Installer: pin pip to `/usr/bin/python3 -m pip` so backfilled deps
land in the same interpreter the verify step uses. Backfill is
FATAL on failure. Post-backfill sanity check imports the modules
from the same interpreter to catch pip's "rc=0 but nothing landed"
lies.**

v0.5.187's certifi backfill still failed on srv01 (Ubuntu 22.04)
because it invoked bare `pip3`, which shimmed to a different
Python than the verify's `/usr/bin/python3`. pip reported success,
certifi went to the wrong site-packages, verify blew up with
`ModuleNotFoundError: No module named 'certifi'`, and the WARNING-
level fallback message never surfaced.

Fixes in [install_ostg_complete.py](install_ostg_complete.py):

* Every `pip3 install …` inside `install_ostg` now uses the local
  `PIP` variable set to `/usr/bin/python3 -m pip`. This includes
  the wheel-install, the deps-install-with-force-reinstall, the
  transitive-dep backfill, and their PEP 668 / distutils retries.
  No more shim mismatch.
* Backfill is FATAL on non-zero rc (was WARNING, which hid the
  problem).
* New post-backfill sanity check runs
  `/usr/bin/python3 -c 'import certifi, urllib3, charset_normalizer, idna; print(certifi.where())'`
  and hard-fails with a diagnostic pointing at the exact
  interpreter mismatch if pip's rc=0 turns out to be a lie.

## [0.5.187] - 2026-07-12

**Two fixes: installer force-installs requests' transitive deps,
AND tarball workflow re-fires on tag pushes so `.tar.gz` shows up
on every release page.**

Both surfaced during a single operator install session on 2026-07:
the fresh install broke on missing certifi, and when we recommended
"switch to the tarball flow", the tarball wasn't on the release
page because v0.5.22 had disabled the tag-trigger for the tarball
workflow to save CI time.

### Tarball workflow — tag trigger re-enabled

* [.github/workflows/build-server-tarball.yml](.github/workflows/build-server-tarball.yml)
  `push.tags: ['v*']` re-added. v0.5.22's "skip on wheel-only
  releases" rationale is real, but the failure mode of "operator
  follows the docs and the tarball doesn't exist" outweighs the
  ~5 min extra CI + ~196 MB storage per tag.
* Added a `concurrency` group keyed on `github.ref` so dev-time
  branch pushes cancel each other in-flight (tag pushes are safe
  because tag refs never re-fire).

### Installer — force-install transitive deps

**Installer force-installs requests' transitive deps (certifi,
urllib3, charset-normalizer, idna) so the post-install verify
doesn't blow up with `ModuleNotFoundError: No module named
'certifi'`.**

Confirmed on srv01 after the v0.5.186 diagnostic improvements
surfaced the actual root cause. Pip installed the wheel + its
direct deps (flask, scapy, requests) but skipped certifi — a
transitive of requests — because an apt-installed
`python3-certifi` was previously on the box and pip's resolver
decided to keep the system-managed one instead of reinstalling.
Then something else removed it. Result: requests couldn't
`from certifi import where`.

Fix in [install_ostg_complete.py](install_ostg_complete.py):
between the wheel-deps install and the verify step, run
`pip3 install --force-reinstall certifi urllib3 charset-normalizer
idna` explicitly. ~500 KB total, idempotent when everything is
already correct, non-fatal on error (verify step catches real
breaks).

## [0.5.186] - 2026-07-12

**Fresh install log: full traceback + accurate diagnostic when the
post-install dep-import check fails.**

Operator hit `Post-install dep import check failed: Traceback …
File "requests/utils.py", line 24, in <module> from .` — traceback
cut mid-line at the exact position that told them what actually
broke, and the installer then blamed a python-version mismatch that
the traceback itself proved wasn't the cause.

Fixes in
[install_ostg_complete.py](install_ostg_complete.py):

* Truncation raised 300 → 8000 chars so full tracebacks survive.
  Same fix on the earlier `deps_result` log (500 → 8000).
* Verify command now imports each dep on its own line and prints
  `py:` / `site-packages:` prefixes, so the log tells you *which*
  module actually failed AND which python/site-packages resolved
  it — no more guessing.
* Full stderr+stdout is also persisted to
  `/tmp/netgen_install/dep-check-failure.log` on the target, so
  the operator can `less` it after the fact even if the client
  log-viewer scrollback rolled off.
* Rewrote the diagnostic message. The old one only mentioned
  python-version mismatch. The new one enumerates the three real
  likely causes in priority order (broken transitive dep like
  urllib3 v2 on old OpenSSL, actual python-version mismatch,
  missing system library) with the exact shell command that
  isolates each one.

## [0.5.185] - 2026-07-12

**Install / Upgrade Server dialog — Fresh install via SSH tab no
longer overlaps rows when the dialog is narrow.**

Operator screenshot showed the Wheel / tarball, Installer, and
install_ostg_complete.py flags rows painting on top of each other
at ~900 px width, with the flags QGroupBox title clipping into the
first checkbox. Three root causes fixed:

* [widgets/install_server_dialog.py](widgets/install_server_dialog.py)
  `_build_fresh_install_tab` — the QFormLayout had no explicit
  spacing, so it inherited the style default (0-6 px on some
  platforms). Set `verticalSpacing(10)` and `horizontalSpacing(12)`
  so rows always breathe.
* Same file — the flags QGroupBox stylesheet had
  `padding-top:6px + margin-top:6px`, smaller than the title-glyph
  height. Bumped both to 14 px (Qt platform default) so the title
  sits above the first checkbox instead of overlapping it.
* Same file — wrapped the whole form in a `QScrollArea` so a
  user-resized narrow dialog scrolls the fields instead of
  clipping the buttons.
* Field-growth policy set to `ExpandingFieldsGrow` and label
  alignment to right/vcenter so the label column stays sized to
  the longest label instead of eating half the dialog width on
  macOS.

4 regression tests in
[tests/test_v05185_install_dialog_layout.py](tests/test_v05185_install_dialog_layout.py).

## [0.5.184] - 2026-07-12

**In-trial license upgrade — Help → License Status gains an
"Activate License…" button so a trial-mode operator can paste a paid
JWT without waiting for the trial to expire.**

Before v0.5.184, the activation dialog was only reachable via the
boot gate. Once a trial was live, `is_activated()` returned True →
the gate short-circuited → the only way to load a paid key was to
wait ~30 days for the trial to expire. Not good.

* [widgets/license_dialog.py](widgets/license_dialog.py) now shows
  an `Activate License…` button alongside Renew and Deactivate.
  Click opens the same `LicenseActivationDialog` used at boot — the
  operator pastes their JWT, `save_license()` writes
  `~/.netgen/license.jwt`, and `load()` prefers the JWT over the
  trial (invariant already covered by
  `test_valid_jwt_takes_priority_over_trial`).
* In trial / grace / invalid states, the Activate button is
  promoted to primary-blue styling with a tooltip explaining the
  upgrade path — same visual weight the Renew button gets when
  close to expiry.
* On successful activation, the License Status dialog refreshes
  in-place (no restart). The main window's chip and banner refresh
  when the dialog closes via the existing v0.5.183 wiring.
* 3 new tests in
  [tests/test_v05183_license.py](tests/test_v05183_license.py):
  trial → paid mid-session, Activate-button urgency in trial,
  Activate-button neutral styling under paid.

## [0.5.183] - 2026-07-12

**Client-side licensing — verified against tlink-license-server offline
codes (RS256 JWT). Plus 30-day self-serve trial, auto-start-streams
gated OFF by default, status-bar chip, top-of-window banner, and an
`netgen-cli license …` subcommand for headless/CI activation.**

Netgen now ships with a bundled RSA public key at
`resources/license/tlink-public.pem`. On launch, a blocking activation
dialog reads the JWT the operator pastes (or loads from file), verifies
the RS256 signature, checks product_code, expiry, and device
fingerprint, then caches the token at `~/.netgen/license.jwt`. The
license unlocks the four flagship features — DPDK Blast, RDMA Blast,
RDMA Topology, RFC 2544. Non-gated features (scapy streams, admin
console, help) stay open regardless.

### New — activation flow
* `widgets/license_activation_dialog.py` — screenshot-shaped blocking
  gate at launch. Multi-line paste area, Load-from-file, Activate,
  Start-30-day-trial, Buy link, fingerprint field with Copy + QR
  buttons (fingerprint is the exact string operators need to paste
  into the tlink-license-server admin UI).
* `widgets/license_dialog.py` — Help → License Status. Read-only view
  of the currently-loaded license (email, tier, billing, start, end,
  session, fingerprint) plus Renew License… (opens `NETGEN_LICENSE_BUY_URL`)
  and Deactivate. Renew gets urgent blue styling when the license is
  ≤30 days from expiry or in the grace period.
* `widgets/license_banner.py` — top-of-window banner shown when the
  license needs attention (≤7 days from expiry, in the post-expiry
  7-day grace period, or invalid). Dismissible per-day. Emits a
  `renew_clicked` signal wired to the buy URL.
* `widgets/license_chip.py` — status-bar pill; 6 states: ✓ Licensed
  (green), ⏱ Trial · Nd left (amber), ⚠ License · Nd (amber),
  ⚠ Unlicensed (red), ⛔ Grace period (red), ⛔ Trial/License expired
  (red). Refreshes every 60 s and on any license mutation.

### New — trial
* `utils/license.start_trial()` — 30-day self-service trial persisted
  as `~/.netgen/trial.json` plus a `~/.netgen/trial-used.marker` so a
  second start returns `already used`. Trial passes `is_activated()`
  and unlocks every gated feature.

### New — headless / CI
* `netgen-cli license status | activate --token <JWT> | activate --file <path> | deactivate | trial | fingerprint`
  wraps the same `utils.license` module the GUI uses. Exit code 0/1
  reflects `is_valid`.
* `NETGEN_LICENSE_TOKEN` env var — take precedence over
  `~/.netgen/license.jwt` for kiosk/CI deployments.
* `NETGEN_LICENSE_FILE` env var — alt file location.
* `/etc/netgen/license.jwt` — machine-wide file fallback.

### New — resilience
* 7-day post-expiry grace period: an entitlement whose `end_date`
  passed within the last 7 days still loads, but flagged as in-grace
  so the banner + chip nag until the operator renews. > 7 days past
  expiry → hard-denied.
* `~/.netgen/license-audit.log` — records every activate/deactivate
  with ISO timestamps, useful for compliance audits.
* Pubkey loader uses `importlib.resources` so it works from wheel,
  tarball, PyInstaller bundle, and repo checkout. Env override for
  operators rotating keys.

### Traffic streams no longer auto-start on launch
Per operator ask 2026-07: streams saved in a session no longer restart
automatically 3 s after the client boots. Gated behind
`QSettings("Netgen", "netgen-client").value("auto_start_streams_on_launch", False)`.
Operators who want the old behaviour flip that key.

### Deps
* PyJWT>=2.8.0, cryptography>=41.0.0, qrcode>=7.4 added to
  `requirements.txt`.

### Tests
* 58 tests in `tests/test_v05183_license.py` covering JWT verify,
  tamper detection, product-code mismatch, session vs entitlement
  expiry, fingerprint match/mismatch, trial start/refuse-restart,
  discovery order (env token / env file / disk), grace-period
  entitlement, audit log persistence, chip states, banner visibility,
  activation dialog + CLI subcommand smoke.

## [0.5.182] - 2026-06-18

**RDMA recheck-audit batch: NB-1 through NB-12 — every gap operator
surfaced from the v0.5.181 srv06 Run-all report.**

Operator ran v0.5.181's Run-all queue with 16 parallel workers on
srv06, exported the report, and surfaced 12 distinct issues spanning
data correctness, queue mechanics, and presentation. This release
fixes every one. The whole batch went through a second independent
self-review before ship — findings from that review are folded in
too (MED-2 spinner restore on Stop, NB-6 behavioural smoke test).

### Data-correctness fixes (HIGH severity)

- **NB-2**: Multi-worker latency tests now capture per-extra
  `lat_avg_us` / `lat_min_us` / `lat_max_us` / `lat_p99_us` —
  previously only worker 0's lat reached the Σ, so a 16-parallel
  lat run averaged 1 sample instead of 16.
- **NB-6**: Run-all queue advances now wait for every extra worker
  to finish, not just worker 0. Operator saw a staircase across
  Run #4/#5/#6 (1/15/16 of 16 done) because the queue advance was
  killing in-flight extras when the next test's Start fired. The
  finalize logic split into `_finalize_run()` is gated by
  `_maybe_emit_total`'s all-workers-done signal.
- **NB-8**: Latency tests now use `-n` (iter count) instead of
  `-D` (duration). Duration mode silently strips perftest's
  9-column output (which carries p99 / min / max / stdev), so
  every srv06 lat report had `p99 = —`. The dialog still respects
  operator's iter count if set.
- **NB-12**: Params snapshot at Start time → `_iteration_params`
  (Blast + Topology). Pre-fix, `_append_run_log_entry` re-read
  spinners at report time — if operator changed a spinner mid-run
  (likely in Run-all's 1.5 s inter-test gap), the report
  misrepresented what was actually used. Likely root of NB-1
  (Run #3 read_bw said Parallel=16 but only 1 worker reported).

### Measurement-quality fixes (HIGH severity)

- **NB-4**: Run-all queue now auto-tunes spinners crossing into
  `*_lat` tests: msg_size→2 B, parallel_workers→1, tx_depth→2.
  Restores the operator's BW-shaped baseline when returning to
  `*_bw`. Operator's "41 µs send_lat" was loaded-latency-under-
  contention (16 flows × 65536 B), not idle. Idle lat on ConnectX-6
  is ~1.5–3 µs; the auto-tune surfaces that number.
- **NB-5**: Status banner explains the auto-tune at lat-test start
  so the operator sees what changed and why.

### Presentation fixes (MED severity)

- **NB-3**: Lat Σ row dispatches on `aggregation_mode` +
  `workers_attempted` / `pairs_attempted` — partial-reporting honesty.
  "1 samples" becomes "1 of 16 workers" when 15 were spawned.
- **NB-7**: Lat Σ row now renders min/max (parens format) when
  the spread is non-zero — operator could not previously see
  jitter without scrolling the per-row table.
- **NB-9**: Endpoint table MTU column populated from `ibv_devinfo`.
  Pre-fix the device payload was cached at dialog-open and never
  refreshed; ports that came up after dialog-open showed `—`.
- **NB-10**: Endpoint table IPv4 column populated from the post-
  preflight test IPs. Same root cause as NB-9 — added a
  `_refresh_device_payloads()` call at Start time in Blast (Topology
  already prefetched per-run via `_prefetch_endpoint_devices`).
- **NB-11**: Single-worker BW headline says "total across 1 worker"
  instead of opaque "final" — consistent with the multi-worker
  phrasing.

### Self-review catches (folded in pre-ship)

- **review MED-2**: Stop mid-queue restores the operator's spinner
  baseline. Previously, stopping inside a `*_lat` test left the
  spinners at msg_size=2 / parallel=1 / tx_depth=2.
- **review LOW-2**: Added a behavioural smoke test for the NB-6
  finalize ordering (stubbed `_finalize_run`, verified
  `_pending_finalize` gate fires correctly).

### Tests

23 new tests in `test_v05182_recheck_audit_batch.py` (including the
behavioural NB-6 smoke). Touched but kept-green: every prior
`test_v05181_*.py` suite, plus `test_v05177_lat_sweep.py` (updated
to recognise the new `-n` over `-D` semantics for lat tests).

### Known limitations (deferred to follow-up release)

- Topology multi-worker-per-pair lat tests still surface "1 sample"
  Σ — same gap as Blast NB-2/NB-6 but in `_pair_extra_workers`
  instead of `_extra_workers`. Out-of-scope for this batch since
  the operator's report didn't exercise it; the NB-4 auto-tune
  forces parallel=1 for lat so this only matters if a user
  manually forces multi-worker lat outside Run-all.

## [0.5.181] - 2026-06-17

**Blast + Topology RDMA — Σ row polish, Topology sweep parity, Run-all
queue, SUM aggregation, Max-BW visibility, and the v0.5.181-rc
gap-recheck batch.**

This release closes the v0.5.180 follow-ups operator surfaced after
seeing the live srv06 reports, plus a self-audit batch (B-1/B-2/B-3 +
P-1 + G-1/G-4) that ran before ship to catch silent regressions the
initial pass missed.

### Σ row polish (#3 / #4 / #5)

- Same-value spread collapses: `avg 156.65 (min 156.65, max 156.65)` →
  `avg 156.65`. Less noise in single-sample runs.
- MsgRate Σ now mirrors BW format: `avg 0.30 (min 0.28, max 0.32)` not
  bare `0.30`.
- Iters Σ column carries `iters_sum` across samples instead of `—`.

### Topology sweep parity (M-RE-1)

- "Sweep sizes (RFC 2544)" checkbox + iterations-per-size spin added to
  the Topology dialog. Visibility gated on `*_lat` test type, mirrors
  Blast.

### Run-all queue mode

- "Run all tests" checkbox in both dialogs cycles `send_bw → write_bw →
  read_bw → send_lat → write_lat → read_lat`, each running `iterations`
  iterations. Combo + checkbox lock while the queue runs; Stop clears
  the queue and re-enables UI.

### SUM aggregation + Max-BW visibility

- Σ summary now dispatches on `aggregation_mode`: parallel workers /
  parallel pairs use SUM (total wire throughput); iterations stay AVG
  (variance). Operator saw `avg 10.28 Gbps` on a 16-parallel-worker
  run when true total was 113 Gbps — root cause: pre-fix code used
  AVG for everything.
- `workers_attempted` / `pairs_attempted` surface "N of M reported"
  honesty when some workers/pairs don't return data.
- 🚀 Max BW button + extras spawn log the picked cpu set + numa pin so
  the operator can see why a fallback (e.g. srv06's NUMA-local
  `[0-15]` equalling the linear `[0-15]`) doesn't change the rate.

### Re-audit catches (H-RE-1 through L-RE-1)

- Live grid header reflects the actual test type (was "BW (Gbps)" for
  lat runs).
- Σ row, results card, and report headline switch to lat columns when
  the run was `*_lat`.
- Line-rate efficiency gate skips lat tests (the % was meaningless on
  ping-pong workloads).
- Topology probe error surface now distinguishes "errored" from
  "timed out" + degrades gracefully when partial.

### Gap-recheck batch (v0.5.181-rc audit)

Self-audit of the above caught five silent bugs before ship:

- **B-1**: sweep checkbox unticked when Run-all crossed `*_bw` tests
  in the queue, losing operator's preference for the later `*_lat`
  tests. Fix: skip auto-untick while a Run-all queue is active.
- **B-2**: Stop during the 1.5 s inter-test pause didn't cancel the
  pending `QTimer.singleShot`, so the next test fired anyway. Fix:
  track the timer reference and `.stop()` it on Stop.
- **B-3**: Blast `iters_sum` always summed to zero because no row
  carried `iters`. Fix: thread `final_iterations` through worker-0,
  per-iter, and per-extra-worker state into the row builders.
- **P-1**: Topology Max-BW button logged the worker math but not the
  picked cpus + numa at click-time. Fix: sibling parity with Blast.
- **G-1**: Run-all progress text was "5 more in queue" — opaque. Fix:
  "Test 2/6 done — next: write_bw".
- **G-4**: Topology summary had no `pairs_attempted` analog to Blast's
  `workers_attempted`. Fix: thread it through summary + report
  renderer ("total across 3 of 4 pairs").

### Tests

35+ new tests across `test_v05181_polish_and_topology_sweep.py`,
`test_v05181_run_all_tests.py`, `test_v05181_aggregation_and_visibility.py`,
and the gap-recheck batch in `test_v05181_gap_recheck_fixes.py`.

## [0.5.180] - 2026-06-17

**Topology dialog lat reporting pipeline — full fix bundle.**

Operator hit two reports back-to-back on srv06:

1. After v0.5.178: "for RDMA topology test, latency test
   failing every time, check the logs … latency test works
   from RDMA blast test." Report showed `rc=0` (perftest
   succeeded) but every result cell rendered `—`.
2. After diagnosing #1 and shipping a partial fix: "i already
   asked to audit rdma topology test, how did you miss this
   bug, pls audit again and find any other bugs."

This release bundles both the original bug fix (v0.5.179 work)
and the deeper re-audit that caught five more lat-pipeline gaps
the v0.5.178 audit missed.

### v0.5.179 work — three sites where lat was being dropped

The v0.5.176 fix made the Blast dialog forward lat fields end-
to-end. The Topology dialog was never updated; lat runs lost
their data at three pipeline sites:

* **`_snapshot_iteration_results`** captured only `bw` /
  `msgrate` from each per-pair job dict. Added
  `lat_avg_us` / `lat_min_us` / `lat_max_us` / `lat_p99_us` /
  `iters` to the snap.
* **`_append_run_log_entry`** row builder forwarded only the BW
  fields. Now mirrors the Blast pattern: `if r.get("lat_avg_us")
  is not None: row["lat_avg_us"] = …` plus a lat-shaped summary
  (`lat_avg_us` / `lat_min_us` / `lat_max_us` / `samples`) when
  any per-pair row carried lat data.
* **`_render_results_card`** dialog headline now dispatches on
  `is_lat_run` and falls to a new `_render_lat_results_card`
  that mirrors Blast's "X.XX µs avg | Y.YY µs p99 · …" layout.

### v0.5.180 re-audit — five sites the v0.5.178 audit missed

After the v0.5.179 fix landed, operator (correctly) called out
that the v0.5.178 audit should have caught these. The audit had
focused on code shape (state init, race conditions, probe
correctness) and never traced data flow from `PerftestJob.final_*`
→ poll → snapshot → render. Re-audit caught:

* **H-RE-1 · `_update_pair_row`**: pre-fix wrote
  `final_bw_avg_gbps` and `final_msg_rate_mpps` to live grid
  columns 4+5 unconditionally. Lat tests showed `—` for the
  ENTIRE run — even though the data was being captured into
  `_latest_jobs`. Now sniffs `final_lat_avg_us` and writes µs
  values when present.
* **H-RE-2 · `_append_summary_row`**: pre-fix had
  `if not bws and not mrs: return` which swallowed every lat
  run's Σ summary row. Lat samples now flow through too.
* **H-RE-3 · `utils/rdma_report._render_headline`**: pre-fix
  always rendered the BW headline — the green callout box at
  the top of every run card said "— Gbps | — Mpps" for lat
  runs even after v0.5.179's per-row dispatch fix. Now routes
  to a new `_render_lat_headline` that carries µs units.
* **H-RE-4 · stats table column headers** (line 654): static
  "BW Gbps / MsgRate Mpps" set once at construction; never
  re-labeled. Even with lat values written into cells, the
  COLUMN labels misled operators. New
  `_refresh_stats_column_headers(is_lat=True/False)` flips the
  labels based on actual data.
* **M-RE-2 · line-rate efficiency calc**: previously ran
  unconditionally; safe today because `bw is None` on lat runs,
  but a stale value would render "% of N G line rate" against
  a µs number. Lat headline now skips the calc entirely.

Plus L-RE-1 (probe-error surface), L-RE-2 (small polish).

### Audit-method debt I'm now carrying

The re-audit was needed because the v0.5.178 audit was missing
two systematic passes:

1. **Data-flow trace per output field** — for each of
   `bw_gbps`, `msgrate_mpps`, `lat_avg_us`, `lat_min_us`,
   `lat_max_us`, `lat_p99_us`, `iters`, `error`: walk
   `PerftestJob.final_*` → REST → `_latest_jobs` → snapshot →
   row dict → summary → renderer (dialog card + report). Any
   gap = bug.
2. **Sibling parity diff** — for parallel widgets (Blast ↔
   Topology, Stream ↔ DPDK Status), every recent fix in one
   is a candidate bug in the other.

Future audits start with these two passes.

### Deferred

* **M-RE-1 · Topology dialog sweep checkbox** — Blast has
  v0.5.177's "Sweep sizes (RFC 2544)" checkbox; Topology
  doesn't. This is a feature gap, not a bug; adding the
  per-pair × per-size sweep matrix renderer deserves its own
  scope. Filed for a future release.

### Tests

16 new tests across two files; 200 pass across the topology +
lat-pipeline regression sweep:

* `tests/test_v05179_topology_lat_report.py` — 7 tests pinning
  the three Blast-parity sites
* `tests/test_v05180_topology_lat_re_audit.py` — 9 tests
  pinning the re-audit fixes (live grid dispatch, summary row
  lat, report headline lat, column header relabel, line-rate
  gate, probe-error surface)

Sample report rendered to
`/tmp/netgen-topology-lat-v05180-sample.html` for visual
review.

### Verification status

Code-level: 200/200 pass. End-to-end on srv06 lands when this
wheel is installed there. The operator's failing report from
v0.5.178 (`rc=0` + every cell `—`) will render correctly:
green "5.54 µs avg | 8.10 µs p99" headline + 5-column lat table
with real numbers.

## [0.5.178] - 2026-06-17

**Three operator-reported RDMA Topology bugs + full audit pass.**

Operator hit three live bugs on srv06 in the v0.5.177 sweep:

1. send_lat sweep with qp_count > 1 → perftest exit rc=1
   "Multiple QPs only available on bw tests".
2. "Another Blast RDMA Flow dialog is already targeting …"
   warning firing after the operator had Stopped the sibling
   dialog's run.
3. Topology dialog crash on first poll —
   `AttributeError: 'RdmaTopologyEndpoint' object has no
   attribute 'hca'`.

After fixing each, a full audit pass over the topology code
(`utils/rdma_topology.py`, `widgets/rdma_topology_dialog.py`,
`traffic_client/rdma_menu_actions.py`) surfaced 11 more real
bugs. Everything bundled here.

### Reported bugs (the three above)

* **qp_count gate**: `_build_perftest_cmd` now requires
  `test.endswith("_bw")` before appending `-q N`. `*_lat`
  tests get the perftest default 1 QP regardless of the
  dialog's qp_count spinbox value.
* **Sibling-conflict idle-only**: the warning's tracker now
  claims an HCA only when at least one side has a job_id set
  AND `_finished` is False. After Stop, `_finished` flips
  True → the claim disappears → no false alarm.
* **`ep.device` not `ep.hca`**: the `RdmaTopologyEndpoint`
  dataclass field is `device`; pre-fix code at
  `_append_run_log_entry` read `ep.hca`, which doesn't exist.

### Audit findings (11 more)

* **H1** (`_mark_pair_failed`): row offset bug — pre-fix wrote
  to `pair_index` directly, overwriting iteration 1's result
  when iteration 2's pair 0 failed.
* **H2** (`_topology_probe_then_start`): added an 8 s
  wall-clock timeout. Pre-fix a single hung probe could leave
  the Start button disabled forever.
* **H3** (probe shape): pre-fix only probed the FIRST same-host
  pair. Mesh / fan-out topologies with multiple same-host
  pairs on DIFFERENT HCAs could miss per-iface blockers (DOWN
  port, missing IP) on HCAs the sample pair didn't touch. Now
  fans out across every unique `(tg_url, device, ib_port)`
  tuple (capped at 12 probes).
* **H4** (CIDR helper): `_build_unique_test_ifaces` centralises
  the test-IP assignments; the pre-fix hardcoded pair
  `(10.42.0.1/24, 10.43.0.1/24)` would CIDR-collide the
  second same-host pair's auto-apply.
* **H5** (`spec_workload`): deleted the dead-code "placeholder
  to satisfy lint" assignment.
* **M1** (`aggregate_stats`): per-pair latency now propagates
  `min_lat_us` / `max_lat_us` / `max_lat_p99_us`. The TOTAL
  line shows worst-case tail across pairs — the Spirent/Ixia
  deliverable that pre-fix was being silently dropped.
* **M2** (`validate_spec`): test type regex
  `^(send|write|read)_(bw|lat)$` — typos like `send_lay` now
  catch at validation, not at perftest stderr.
* **M3** (`validate_spec`): port range overflow — pre-fix
  bounded `base_listen_port ≤ 65000` but a 25×25 mesh from
  base 64950 expanded to 65574 which overflows 65535.
* **M5** (`_on_job_resp`): O(1) `{job_id: pair_index}` reverse
  index instead of scanning `_plans` every poll. Matters on
  100-pair meshes (50 polls/sec × scan).
* **M6** (`_render_results_card`): None-guards on `bw_min` /
  `bw_max` — pre-fix could crash the headline render when
  every per-pair bw came back None (all-pairs-errored case).
* **L1** (init hygiene): `_current_iter_base_row`,
  `_pair_extra_workers`, `_iteration_results`,
  `_iteration_idx`, `_iterations_total`, `_stop_requested`,
  `_spec`, `_topology_probe_buf` now initialised in
  `__init__`. The previously-broken
  `test_stats_table_populates_skeleton` started passing
  without any test change — exactly the right kind of test fix.
* **L2** (`_endpoint_device_cache`): skip cache entries with
  no name field instead of storing them under `"?"` where
  they'd shadow each other.

### State-management races also closed

* **M4 + M8** (`_run_one_iteration`): the new iteration boundary
  calls `_stop_poll()` first, drops outstanding callbacks
  before resetting `_pair_jobs` / `_latest_jobs`. Pre-fix a
  late callback from iteration N could write into the FRESH
  `_latest_jobs` map iteration N+1 just reset, leaking the
  old job_id forever.

### Tests

33 new tests across 4 new files:

* `tests/test_v05178_qp_count_lat_gate.py` — 9 tests
* `tests/test_v05178_sibling_conflict_live_only.py` — 7 tests
* `tests/test_v05178_topology_endpoint_attr.py` — 3 tests
* `tests/test_v05178_topology_audit.py` — 14 tests

The pre-existing
`tests/test_rdma_topology_dialog.py::test_stats_table_populates_skeleton`
went from FAILED to PASSED via the L1 init fix — no test
change.

231/231 RDMA / topology / lat-sweep tests pass.

### What this does NOT include

This release is bug-fix only; no operator-visible UI changes
beyond the better warning copy on probe timeouts and the
wider TOTAL line on latency runs (now shows spread + worst
p99). The Spirent/Ixia-style latency sweep from v0.5.177 is
unchanged.

## [0.5.177] - 2026-06-17

**Spirent/Ixia-style RDMA latency characterization + send_lat
duration-mode parser fix.**

Operator: "lets implement spirent/ixia type behavior for rdma
latency test and provide clear reporting" — plus end-to-end
verification on srv06 had already proved that even single-size
latency runs were silently dropping every sample because the
existing parser only recognised perftest's 9-column
iteration-mode output, not the 4-column shape `-D N` actually
emits.

### 1 — send_lat / read_lat / write_lat parser fix

Real srv06 stdout for `ib_send_lat -D 30`:

```
#bytes        #iterations       t_avg[usec]    tps average
2             1577611            1.90           262864.28
```

Only 4 columns — no min / max / typ / stdev / p99 / p99.9. The
existing `_RE_LAT_DATA_ROW` required all 9 columns, so every
single-size latency run from the Blast dialog left every
`final_lat_*` field as `None`. v0.5.176's client-side capture
was correct but had nothing to capture.

Fix: new `_RE_LAT_DATA_ROW_DURATION` regex with a duration-mode
fallback in the stdout reader. When the 9-col regex misses, the
4-col regex matches and populates `final_lat_avg_us`. min/max/
p99 stay `None` and render as `—` (honest), instead of being
silently dropped along with avg.

### 2 — RFC 2544-style latency-vs-size sweep

`perftest -a` cycles through every power-of-two message size
from 2 B to 8 MB and emits one row per size with the full
min/typ/avg/max/stdev/p99/p99.9 spread. This is what Spirent
and Ixia call a "latency-vs-size curve" and is the right way
to characterise an RDMA fabric.

* **Backend** (`utils/rdma_perf.py`): new `sweep_sizes: bool`
  + `iterations_per_size: int` start-req fields. When set, cmd
  builder appends `-a -n <N>` and suppresses `-D` and `-s`.
  Stdout reader accumulates EVERY 9-col row into
  `PerftestJob.final_lat_sweep: List[Dict]`, each entry
  carrying bytes / iters / lat_min / lat_max / lat_typ /
  lat_avg / lat_stdev / lat_p99 / lat_p999.
* **GUI** (`widgets/rdma_blast_flow_dialog.py`): new
  "Sweep sizes (RFC 2544)" checkbox + iterations-per-size
  spinbox (default 5000). Visible only when test type is a
  `*_lat`. When ticked, Message size + Duration grey out
  (perftest ignores both under `-a`). Auto-unticks if the
  operator switches back to a `*_bw` test.
* **HTML report** (`utils/rdma_report.py`): new section
  "Latency vs Message Size (RFC 2544-style)" with an inline
  SVG line chart (log-x message size, linear-y µs, solid
  green avg line, dashed amber p99, light green min↔max
  envelope) followed by a per-size 9-column table
  (Size · Iters · Min · Typ · Avg · Max · StdDev · p99 ·
  p99.9). No JS, no external assets — the report stays
  self-contained for archival.
* **Plumbing**: dialog mirrors `final_lat_sweep` into a new
  `_client_lat_sweep` instance var, the run-log entry carries
  the sweep payload, and the report builder appends the
  sweep section only when present (legacy / single-size runs
  unchanged).

### Tests (87 lat-related, all green)

* `tests/test_v05177_lat_duration_mode_regex.py`: pinned to
  srv06's verbatim 4-col stdout, asserts the new regex
  extracts `t_avg=1.90`; verifies the 9-col regex still
  matches its old shape and does NOT match the 4-col shape.
* `tests/test_v05177_lat_sweep.py`: regex matches each row in
  a multi-size sweep; cmd builder emits `-a -n N` (not `-D`
  / `-s`) under sweep mode; default `iterations_per_size`
  = 5000; `PerftestJob.final_lat_sweep` round-trips through
  `to_public_dict`.
* `tests/test_v05177_lat_sweep_report.py`: full
  `build_html_report` integration — sweep run renders the
  chart + 9-column table; non-sweep BW run leaves no sweep
  section (no "Latency vs Message Size" header on every
  BW report). Chart degrades to avg-only when min/max
  absent. No `<script>` or `<img>` tags — self-contained.

### Verification status

Code-level: 87/87 tests pass. Regex matches the exact srv06
duration-mode stdout. Sample sweep report rendered to
`/tmp/netgen-lat-sweep-sample.html` for visual review.

End-to-end on srv06 lands when this wheel is installed there.

## [0.5.176] - 2026-06-17

**Two RDMA report bugs: lat tests and read_bw.**

Operator: "check the report, seems read_bw and send_lat is not
working correctly also check other Test types if they are
working fine."

### Bug 1 — latency tests showed all dashes

The Blast dialog only stashed `_client_bw` and `_client_msgrate`
when the client side finished. It never captured the lat fields.
The run-log iter rows therefore had no `lat_avg_us` field; the
report's `has_lat = any(... "lat_avg_us" ...)` dispatch fell
through to BW rendering with all `—` cells for every latency
run (`send_lat`, `write_lat`, `read_lat`).

Fix:

* New `_client_lat_avg_us` / `_client_lat_min_us` /
  `_client_lat_max_us` / `_client_lat_p99_us` instance vars,
  cleared in `_proceed_with_start` and `closeEvent`.
* Each per-iter `_iteration_results.append(...)` now carries
  the lat fields alongside bw/msgrate.
* `_append_run_log_entry` forwards lat fields into the row
  dicts when present.
* Summary aggregator computes lat avg/min/max samples for
  iterate-N lat runs.
* `_render_results_card` now dispatches on `is_lat_run` and
  routes to a new `_render_lat_results_card` that renders the
  marquee as `X.YZ µs avg | A.BC µs p99 · …` instead of BW.

### Bug 2 — read_bw also showed dashes

Some perftest builds emit the BW-peak column as `N/A` for
`ib_read_bw` because peak isn't computed for one-sided RDMA
ops. The strict `[\d.]+` peak regex rejected the entire data
row, leaving `final_bw_avg_gbps` as None.

Fix:

* `_RE_BW_DATA_ROW` peak group widened to `[\d.]+|N/A|-`.
* The stdout reader wraps `float(peak_raw)` in a try/except —
  `N/A` / `-` becomes `final_bw_peak_gbps = None`, but
  `final_bw_avg_gbps` and `final_msg_rate_mpps` are still
  populated, so the report shows the real avg + msgrate.

### Cross-test-type sanity test

Bundled test iterates every supported `_SUPPORTED_TESTS` entry
through `_build_perftest_cmd` and asserts:

* The tool binary basename matches (`ib_send_bw`, `ib_read_bw`,
  `ib_send_lat`, etc).
* `--report_gbits` is added for `_bw` tests AND ONLY for `_bw`
  tests. (Lat tools reject the flag.)
* The peer-address tail is appended for client invocations.

Prevents future regressions where one test type silently drops
out of the cmd builder.

### Files touched

* `widgets/rdma_blast_flow_dialog.py` — lat instance vars +
  capture from client job + thread into iter_results + forward
  into rows + summary aggregator + `_render_lat_results_card`.
* `utils/rdma_perf.py` — `_RE_BW_DATA_ROW` peak alternation +
  defensive `float(peak_raw)` in the stdout reader.
* `tests/test_v05176_lat_capture_and_read_bw.py` — 12
  assertions covering both bugs + the cross-test cmd sanity.

### Test-type matrix after this release

| Test         | BW report   | Latency report   | Status |
|--------------|-------------|------------------|--------|
| send_bw      | works       | n/a              | ✓      |
| write_bw     | works       | n/a              | ✓      |
| read_bw      | **fixed**   | n/a              | ✓      |
| send_lat     | n/a         | **fixed**        | ✓      |
| write_lat    | n/a         | **fixed**        | ✓      |
| read_lat     | n/a         | **fixed**        | ✓      |

## [0.5.175] - 2026-06-17

**Stop DNS-resolution hangs from freezing the GUI.**

Operator pasted a traceback ending in `KeyboardInterrupt` inside
`socket.getaddrinfo` — the EVPN active-injections chip was doing
a sync `requests.get(timeout=5)` on the UI thread every 30 s.
When `san-hp-srv06` stopped resolving (VPN dropped / lab host
off network), macOS `getaddrinfo` blocked for 30+ seconds at the
OS level, freezing Qt's event loop. The `timeout=5` arg only
bounds the post-resolve connect+read; it doesn't touch
`getaddrinfo` itself. Operator had to Ctrl+C the GUI to recover.

### Two fixes

**1. EVPN chip moved to async fetch** — same pattern as the DPDK
readiness chip (v0.4.7) and the orphan chip (v0.5.169):
`_EvpnFetchThread` runs the GET on a one-shot QThread; results
land back on the UI thread via signal. `_fetch_in_flight` dedup
guard prevents rapid timer ticks (or DNS slow-fail stacking)
from queueing concurrent fetches. Transient failures leave the
previous count visible — no UX blink.

**2. Global socket timeout in client entry** —
`socket.setdefaulttimeout(8.0)` at the top of `main()` in
`run_tgen_client.py`. Bounds DNS-resolution time at 8 s for
ANY remaining sync path (one-shot dialogs, future widgets that
haven't been audited yet). 8 s is tight enough to never freeze
the GUI noticeably, generous enough to survive a slow LAN.

### Files touched

* `widgets/evpn_active_chip.py` — `_EvpnFetchThread` class +
  async `refresh()`. The synchronous request path is gone.
* `run_tgen_client.py` — `socket.setdefaulttimeout(8.0)` at the
  top of `main()`.
* `tests/test_v05175_evpn_chip_async_and_socket_timeout.py` —
  7 assertions: source-level (no sync request in `refresh()`,
  dedup guard present, transient-failure UX), widget
  construction smoke (refresh returns immediately), and the
  global-timeout call signature.

### Audit notes (not addressed here)

Other periodic-poll widgets that already use the async pattern
and don't freeze: `dpdk_readiness_chip.py`, `orphan_chip.py`.
One-shot dialog widgets that still use sync `requests.get` are
defended by the global socket timeout — if they hang, the cap
is 8 s, not 30+. A broader async-fetch sweep across all
widgets is a separate follow-up.

## [0.5.174] - 2026-06-17

**Blast RDMA Flow dialog widened to 920 px.**

Operator screenshot showed the results card headline
(`✓ send_bw 162.76 Gbps | 0.3104 Mpps average across 3 samples
· min 162.67 / max 162.93 Gbps · 2 iterations · 30s per run`)
wrapping into two lines, and the "Wrote 2 run(s) to
/Users/.../netgen-blast-report-<ts>.html" status banner
wrapping into three.

Bumped `setMinimumWidth(720) → setMinimumWidth(920)` in the
Blast dialog constructor. 920 keeps the Parameters grid
balanced across 2 columns without forcing horizontal scroll on
stock macOS 13/14 windows.

### Files touched

* `widgets/rdma_blast_flow_dialog.py` — minimum width 720→920.

## [0.5.173] - 2026-06-17

**Blast report: drop the redundant worker 0 row when iter rows
already cover it.**

Operator: "ran itr 1, than ran two itrs, however report picked
up first and last."

The 2-iter Run #2 was producing rows:

  - iter #0  = 162.93
  - iter #1  = 162.67
  - **worker 0 = 162.67**  ← duplicate of iter #1's value

The trailing "worker 0" row always ran the latest `_client_bw`
through, so for iterate-N runs it just duplicated the last
iter's value. Operator visually parsed this as "first" (iter #0)
and "last" (worker 0) — the middle iter #1 row was swallowed
visually because it had the same value as the worker 0 row that
followed it.

Also broke the Σ samples count — for a 2-iter run it showed
"3 samples avg=162.76 min=162.67 max=162.93" instead of
"2 samples avg=162.80 min=162.67 max=162.93".

### Fix

`_append_run_log_entry` now only emits the trailing worker 0 row
when it adds information:

  1. **Single-iter run without extras** — worker 0 IS the result
     (iter rows produced nothing). Row emitted.
  2. **Parallel run with extras** — worker 0 is the
     iteration-0 half of the per-worker breakdown the extras
     started. Row emitted.
  3. **Iterate-N without extras** — iter rows are the complete
     breakdown. **Row suppressed.**

### Files touched

* `widgets/rdma_blast_flow_dialog.py` — `_append_run_log_entry`
  gates the trailing worker 0 row on
  `extras or not already_have_iter_rows`.
* `tests/test_v05173_no_dup_worker0_row.py` — 6 assertions
  covering single-iter, iterate-N, parallel, and mixed cases.

## [0.5.172] - 2026-06-17

**Three operator-reported fixes.**

### 1. Clear Stats now actually clears Packets Lost / Loss %

`clear_cached_statistics` snapshotted tx/rx/sent_bytes/
received_bytes/errors as the tare baseline, but missed `phy_tx`
and `phy_rx` — which is what drives the Packets Lost / Loss %
columns. The loss math (`(pair_tx - pair_rx) / pair_tx × 100`)
kept reading cumulative-since-iface-up PHY counters, so:

* **Packets Lost** showed millions left over from prior runs.
* **Loss %** computed `(24 M / multi-trillion-since-boot) × 100`,
  which rounded to `0.00%` with the 2-decimal format — the
  inconsistency the operator hit on srv06.

Fix: `_iface_baselines` now also tares `phy_tx` + `phy_rx`. The
loss renderer (line ~1867 of `statistics_section.py`) subtracts
both the iface's own baseline and each peer's baseline before
calling `compute_iface_pair_loss`. After Clear Stats both
columns now read "delta since the click" like every other
cumulative column.

### 2. Blast report no longer shows duplicate runs

`_on_both_finished` had no idempotency guard. Once the poll
timer was stopped, Qt-queued poll callbacks already in the
event loop kept entering the function — each re-evaluated
`s_done and c_done` (both True now), re-fired iteration logic,
and **re-appended a second entry to `_run_log`**. Operator ran
one test, got two runs in the exported report.

Fix: short-circuit at the top of `_on_both_finished` on a
`_finalised` flag. `_proceed_with_start` clears it on every
fresh Start so each new run can fire exactly once.

### 3. Endpoint table no longer overflows the run-card

The endpoint table grew to 16 columns in v0.5.167 + v0.5.170
(Side / TG / HCA / Model / Link / Rate / MTU / State / PCIe /
NUMA / NetDev / IPv4 / Driver / Vendor / FW / GID). On the
default 1180px run-card width the rightmost columns spilled
past the border.

Fix: wrap the table in a `<div class='endpoint-scroll'>` with
`overflow-x: auto` + `min-width: max-content` on the inner
table. The run-card border stays tidy and the table pans inside
it. Native browser scrollbar shows only when needed.

### Bonus — board_id map gained ConnectX-6

Operator's lab HCA reported `MT_0000000225` (FW 20.40.x) and
the Model column rendered as `—`. Added the mapping — now shows
`ConnectX-6` like the other revisions.

### Files touched

* `traffic_client/statistics_section.py` — PHY baseline in
  `clear_cached_statistics` + own/peer subtraction in the loss
  renderer.
* `widgets/rdma_blast_flow_dialog.py` — `_finalised` guard +
  reset on Start.
* `utils/rdma_report.py` — `endpoint-scroll` wrapper + CSS,
  `MT_0000000225` → `ConnectX-6`.
* `tests/test_v05172_clear_stats_and_report_fixes.py` — 9
  assertions across all three fixes + the new board_id.

## [0.5.171] - 2026-06-16

**Admin portal: one-click HTML report export.**

Operator: "also allow user to generate report for admin portal
http://san-hp-srv06:5050/admin"

The admin page already had **Export Diagnostics** for a tar.gz
of raw artefacts (lspci, ethtool, journal slices) — meant for
engineering deep-dives. v0.5.171 adds a sibling **📄 Export
Report** button for a human-readable HTML snapshot of the
admin-portal state, designed for incident triage, sharing, and
archival.

### What's in the report

* **Header** — hostname, server port, netgen version, generated_at
* **Server health** — DPDK installed? IOMMU enabled? vfio-pci /
  vfio_iommu_type1 loaded? tx_worker binary present? Install
  running? Each row has a green-OK / red-MISSING pill.
* **CLI tools present** — `ip`, `ethtool`, `lldpcli`, `lspci`,
  `ibv_devinfo`, `perftest`, `dpdk-devbind`. Same pill scheme.
* **Hugepages** — per-NUMA total / free / page size.
* **Network interfaces** — iface, driver, MAC, IPv4, speed, MTU,
  link state (UP/DOWN/UNKNOWN badge), NUMA, PCIe (Gen4 x16 with
  yellow `pcie-warn` badge when downgraded), NIC model.
* **RDMA HCAs** — HCA, model, driver, vendor, FW, link layer,
  link rate, MTU, port state, NUMA, PCIe, netdevs, IPv4, GID.
* **DPDK bind history** — last 40 bind/unbind events with
  timestamp + from/to driver.
* **🧟 Orphan workers** — rendered only when any exist; PID,
  role, stream-id, BDF, elapsed time, cmdline. Cross-references
  v0.5.168/169's orphan handling.

### Files touched

* `utils/admin_report.py` (new) — pure-function
  `build_admin_report_html(snapshot, generated_at,
  server_version)`. Section-per-helper structure mirrors
  `utils/rdma_report.py`. Self-contained CSS (inline `<style>`
  block), no external assets. Reuses `_resolve_nic_model` from
  the RDMA report for the board_id → product-name mapping.
* `run_tgen_server.py`:
  * `GET /api/admin/report.html` route — uses Flask
    `test_client()` to fan-fetch `/api/admin/health`,
    `/api/interfaces`, `/api/rdma/devices`,
    `/api/admin/bind_history`, `/api/streams/orphans`. Merges
    into one snapshot and hands to the renderer. Returns the
    HTML as `text/html` with a `Content-Disposition: attachment`
    header so the browser downloads it instead of rendering
    inline. Filename: `netgen-admin-report-<hostname>-<ts>.html`.
  * Admin page (`_ADMIN_HTML`): adds `📄 Export Report` button
    next to `Export Diagnostics`, plus a click handler that
    fetches the endpoint, pulls the filename from
    `Content-Disposition`, and triggers a browser download via
    `URL.createObjectURL` + `<a>.download`.
* `tests/test_v05171_admin_report.py` — 19 assertions covering
  full + empty snapshots, per-section content, HTML escape,
  route source-level checks, and admin-page wiring.

### Why a separate endpoint from `/api/admin/diag_bundle`?

* `diag_bundle` = tar.gz of raw artefacts (lspci, ethtool dumps,
  journal slices, /proc snapshots). Optimised for engineering
  deep-dives — operators forward to support tickets.
* `report.html` = human-readable HTML rendered for a quick scan.
  Mirrors the RDMA session report's design vocabulary.

Both stay — one click per intent.

## [0.5.170] - 2026-06-16

**Report: PCIe Gen + NUMA + IPv4 + NIC model + line-rate
efficiency.**

Operator: "report should also capture the PCI gen used, example
gen4/5/6..etc in the Endpoints. also check if there is anything
else missing in the reports." This release adds the explicit
PCIe ask plus four more high-signal fields the audit surfaced.

### New endpoint columns

| Column | Source | Why it matters |
|--------|--------|----------------|
| **Model** | board_id mapped via lookup | Operators read "ConnectX-7", not `MT_0000000838`. Mellanox MT_0000000200 → ConnectX-3 Pro through MT_0000001019 → ConnectX-8, plus Broadcom Thor/Thor2 and AMD Pollara. Falls through to `—` when unknown. |
| **PCIe** | `/sys/bus/pci/devices/<BDF>/current_link_*` | `Gen4 x16`. When the slot trained below its cap (e.g. Gen5 slot stuck at Gen4 x16, or x16 slot stuck at x8), shows `Gen4 x16 (max Gen5 x16)` with a yellow `.pcie-warn` badge — operator-critical signal that explains BW shortfalls without an SSH session. |
| **NUMA** | `/sys/bus/pci/devices/<BDF>/numa_node` | `node 0`. Lets operators correlate worker placement with HCA NUMA — cross-NUMA single-QP RoCE is a known perf cliff. |
| **IPv4** | `psutil.net_if_addrs()` on the HCA's netdev(s) | `10.43.0.2/24`. Operators cross-reference IPs not GIDs. Picks the first IPv4 across all bonded netdevs. |

### New headline element

* **Line-rate efficiency** — appends `· 86.1% of 200 G line rate`
  to the headline tail. The rate is the SLOWEST endpoint in the
  pair (a 200 G ↔ 100 G run is capped at 100 G end-to-end). One
  glance answers the operator's #1 post-run question: "did we
  hit line rate?"

### Files touched

* `utils/rdma_perf.py` — RdmaDevice gains `pcie_current_speed_gts`,
  `pcie_current_width`, `pcie_max_speed_gts`, `pcie_max_width`,
  `pcie_gen`, `pcie_max_gen`, `pcie_downgraded`, `numa_node`,
  `netdev_ips`. Helpers: `_read_pcie_link`, `_read_numa_node`,
  `_read_iface_ips` (psutil-based), `_resolve_bdf_for_hca`,
  `_parse_link_speed_gts`, `_gts_to_gen`. Flows through
  `/api/rdma/devices` via the existing `asdict` — no route
  change needed.
* `utils/rdma_report.py` — endpoint table gains 4 new columns,
  PCIe downgrade CSS badge, `_resolve_nic_model` board_id
  mapping, `_extract_line_rate_gbps`, headline efficiency line.
* `tests/test_v05170_pcie_numa_ip.py` — 24 source + sysfs-mock +
  render assertions including downgrade detection and
  slowest-endpoint line-rate cap.

### Considered but not added (operator can ask)

* Per-iteration timestamps (would bloat multi-iter reports).
* perftest stderr tail on rc!=0 (not always available client-side
  — would need a server-side capture).
* CPU utilization column when cpu_util enabled (perftest's number
  is a single host-level percentage, not per-core).
* Hostname / kernel / netgen version (already in the header line).

## [0.5.169] - 2026-06-16

**Orphan prevention (systemd-scope) + auto-detect status-bar chip.**

v0.5.168 made orphans visible + reapable. v0.5.169 closes the
loop on both ends:

1. **Prevention** — each tx/rx_worker spawn now goes through
   `systemd-run --scope --unit=netgen-{tx,rx}-<stream_id>.scope`.
   The kernel guarantees lifecycle tracking: a stop is signal
   delivery via cgroup (no PID guessing, no pgrep racing); after
   an ostg-server crash, surviving units are enumerable via
   `systemctl list-units 'netgen-{tx,rx}-*.scope'` so the next
   reaper finds every previous-session orphan in one call.
2. **Auto-detect** — a `🧟 N orphans` permanent status-bar chip
   polls every registered TG's `/api/streams/orphans` every 10 s.
   Hidden when zero. Click → enumeration + Reap-All dialog.
   Operators no longer need to ssh + ps to discover untracked
   workers eating their HCAs.

### Files touched

* `utils/systemd_scope.py` (new) — pure-function `has_systemd_run`,
  `sanitise_unit_name`, `build_systemd_run_prefix`,
  `stop_scope_for_stream`, `list_netgen_scopes`. Hosts without
  systemd-run get an empty prefix → naked Popen fallback (v0.5.168
  reactive reap still applies).
* `utils/dpdk_tx_worker.py` — wraps the main tx_worker Popen.
* `utils/dpdk_rx_worker.py` — wraps the rx_worker Popen.
* `utils/dpdk_tx_worker_multi.py` — per-instance scope wrap so
  multi-instance fan-out gives each tx_worker its own unit.
* `widgets/orphan_chip.py` (new) — `OrphanChip` permanent status-
  bar widget + `OrphanReapDialog` modal that lists orphans
  (PID/role/stream-id/BDF/etime/cmdline) and reaps them all in
  one POST.
* `traffic_client/main.py` — wires the chip into `statusBar()`
  alongside the existing DPDK readiness chip.
* `tests/test_v05169_systemd_scope.py` — 21 source-level +
  unittest.mock + widget construction assertions.

### Flags chosen for systemd-run

* `--scope` — direct ancestor cgroup, no service unit layer.
* `--collect` — auto-cleanup the unit when the scope exits
  (otherwise systemd keeps a failed-state record indefinitely).
* `--unit=netgen-{tx,rx}-<sid>` — predictable name we can
  `systemctl stop` later without grepping `list-units`.
* `--quiet` — no "Running scope as unit..." log spam.

### Operator UX

* **Before**: orphan eats HCA → operator's next test silently
  drops BW from 200 G to 68 G → 30 min of debugging → SSH +
  ps + manual `kill -9`.
* **Now**: status bar shows `🧟 1 orphan` within 10 s of opening
  the GUI → one click → list → Reap All → done. Or just don't
  notice until Start All / Stop All sweeps them automatically
  (v0.5.168).

## [0.5.168] - 2026-06-16

**Orphan tx_worker / rx_worker handling — Stop-All sweeps, Start
pre-flight blocks collisions.**

Operator hit this on srv06: a DPDK Blast stream left a tx_worker
(897% CPU) + rx_worker (798% CPU) running after the prior GUI
session disconnected. The orphans were pinned to the same HCA
the operator was using for RDMA — ~17 cores on NUMA 0 + PCIe
contention on BDF 0000:2b:00.0 — and dropped the RDMA BW from
171 Gbps to 68.59 Gbps. The GUI's "Stop All" had no idea the
workers were alive (untracked stream_id), so it couldn't help.

### What's added

* **`utils/dpdk_orphans.py`** — pure-function module. Walks
  `/proc/*/cmdline`, classifies tx_worker / rx_worker by argv[0]
  basename, parses `--stream-id` / `-a <BDF>` / `--file-prefix`
  via tight regexes. `find_orphans(tracker_ids)` cross-references
  against the active StreamTracker; `find_orphans_for_bdf(bdf)`
  narrows to one device. `reap_workers(pids)` does
  SIGTERM → 1 s wait → SIGKILL with detailed `{terminated,
  killed, failed}` return.
* **`GET /api/streams/orphans`** — returns
  `{orphans, known_stream_ids, total_workers}`. Optional
  `?bdf=0000:2b:00.0` query narrows to one PCI device for
  Start-time pre-flight collision checks.
* **`POST /api/streams/orphans/reap`** — body `{pids: [...]}`,
  refuses empty or non-int pids with 400. Idempotent — re-reaping
  a dead PID is a no-op. Logs every reap with PID set so
  post-mortems are possible.

### GUI changes

* **Stop All Streams** now probes every registered server for
  orphans BEFORE deciding "nothing to stop". If any are found, a
  confirm dialog enumerates per-server:
  > Stop 3 running streams.
  > Reap 2 orphan workers:
  > • http://san-hp-srv06:5050
  >     PID 3194868 (tx_worker) · BDF 0000:2b:00.0 · stream
  >     3ede73ca… · running 13m
  >     PID 3194724 (rx_worker) · BDF 0000:2b:00.1 · stream
  >     3ede73ca… · running 13m
  >
  > Continue?

  On confirm, tracked streams are stopped + orphan PIDs are
  reaped on each server. Any reap failures surface in a follow-up
  warning so the operator isn't misled into thinking the host is
  clean.

* **Start Stream** now probes for orphan workers bound to the
  target NIC before firing the start request. If a collision is
  detected, a "🧹 Reap && Start" / "Cancel" modal asks the
  operator. Cancel drops that server from the start batch (UI
  flips back to red) so the new stream never spawns alongside
  the orphan — the BW-drop case is prevented at the source.

### Files touched

* `utils/dpdk_orphans.py` (new)
* `run_tgen_server.py` — two new routes
* `traffic_client/stream_logic.py` — 6 new helpers
  (`_fetch_orphans`, `_reap_orphans`, `_orphans_touch_iface`,
  `_format_orphan_line`, `_confirm_stop_with_orphans`,
  `_confirm_reap_before_start`); Stop-All + Start wiring
* `tests/test_v05168_orphans.py` — 20 source-level + filesystem-
  mock + real-subprocess assertions

### Known limitation (separate ship, v0.5.169)

Orphans still happen because tx_worker / rx_worker spawn as
detached children — a server crash or signal loss can leak
them. v0.5.169 will wrap each spawn in `systemd-run --scope
--unit=netgen-tx-<stream_id>.scope` so the kernel guarantees
lifecycle tracking (Stop always works; survives ostg-server
restart; orphans become structurally impossible).

## [0.5.167] - 2026-06-16

**Enriched + redesigned HTML session report — NIC details +
visibility pass.**

Operator: "also add more details in the report, example NIC
type, BW, driver,... etc. and also improve the visibility of
report." The v0.5.163 report had two problems: (1) almost no
device detail beyond `TG 0 rocep43s0f0`, and (2) the
`<dl class='params'>` grid with `auto-fill` columns spread `<dt>`
and `<dd>` as independent cells, interleaving labels and values.

### What's new

* **Endpoint detail table** — per-side row showing HCA, link
  layer (Ethernet / IB), link rate (e.g. `200 Gb/sec`), active
  MTU, port state badge, kernel netdev names, driver, board
  vendor, FW version, and the first GID.
* **`RdmaDevice.driver`** — read from
  `/sys/class/infiniband/X/device/driver` symlink and surfaced
  via `/api/rdma/devices` so the GUI gets it for free.
* **Device payload cache** in both dialogs:
  * Blast: `_device_payloads[side][hca]` populated from each
    `/api/rdma/devices` response.
  * Topology: `_endpoint_device_cache[tg_url][hca]` populated by
    a lazy prefetch on Start (one probe per unique TG, runs in
    parallel with perftest).
* **Run-log entry** gains an `endpoint_details` list with the
  rich per-side device dump.

### Visibility pass

* Param table now uses paired `<tr>` rows (not the buggy grid).
* Human labels + units — `Message size` 65536 B, `Duration` 30 s.
* bool fields render as `yes` / `no` instead of `True` / `False`.
* Key-result callout at the top of each run card — mirrors the
  in-app post-run summary card (28px BW + MsgRate headline).
* Run card gets a left accent stripe — clear visual divider in
  multi-run reports.
* Zebra-stripe endpoint + result tables.
* Color-coded port-state badge (ACTIVE / INIT / DOWN).
* Self-contained footer.

### Files touched

* `utils/rdma_perf.py` — `RdmaDevice.driver` field +
  `_read_driver_name()` sysfs helper.
* `utils/rdma_report.py` — full rewrite (CSS + render path).
* `widgets/rdma_blast_flow_dialog.py` — `_device_payloads` cache
  + `endpoint_details` in run-log entries.
* `widgets/rdma_topology_dialog.py` — `_endpoint_device_cache`
  + `_prefetch_endpoint_devices` + `endpoint_details` (deduped
  by side+tg+hca across pairs).
* `tests/test_v05167_report_details.py` — 14 source-level +
  filesystem-mock assertions.
* `tests/test_v05163_html_report.py` — updated to match the new
  `Message size` label (was `msg_size`).

## [0.5.166] - 2026-06-16

**Compact the v0.5.165 results card + status text — single-line
layout instead of three vertical sections.**

Operator screenshot: "wasting lot of space in the dialog, make
it compact." The card was 3 stacked rows (uppercase label /
big-number / extras) plus generous 10×14 padding; the
"Both halves running. handshake=… server_job=… client_job=…"
status wrapped to 3 lines.

### What's compact now

* **Results card** — one row: `✓ <test>  <BW> Gbps | <MsgRate>
  Mpps  <tail>`; padding 4×8 (was 10×14); border-radius 4
  (was 6); BW headline 18px (was 28px); MsgRate 14px (was 18px)
* **Running status** — `Running · hs=… · sjob=… · cjob=…`
  (was a multi-line "Both halves running. handshake=…" block)
* **Finished status** — `Run finished — click Stop or close to
  forget the pairing.` (was a 2-line "Both halves finished.
  Click Stop to forget the pairing (or close this dialog).")

The card still reads from the same `_run_log` entry the Export
Report button uses; no data path changes.

### Files touched

* `widgets/rdma_blast_flow_dialog.py` — card stylesheet padding/
  radius, render HTML rewritten to single inline row, running +
  finished status strings
* `widgets/rdma_topology_dialog.py` — same card stylesheet +
  render HTML
* `tests/test_v05165_results_card.py` — updated headline-font
  test (28/18 → 18/14) to match compaction

## [0.5.165] - 2026-06-16

**Post-run results summary card — headline BW + MsgRate above
the per-pair grid.**

Operator: "post run complete visualizaiton, this is also not
good." After a run finished, the dialog just dumped raw
`[client] done (rc=0) ... BW avg=172.22 Gbps` lines at the
bottom of Live stats — wrapped awkwardly, easy to miss the
actual number.

### What's added

Both dialogs grew a green pill card between the progress strip
and the Live stats / Results panel:

* Big-number BW (28px) + Gbps label
* MsgRate (18px) + Mpps label
* Run summary line — `final` for one-shot runs, or `average
  across N samples` for multi-iteration / multi-pair runs
* Extras line — min/max BW, pair count, iterations, workers,
  duration

The card consumes the same `_run_log` entry that the Export
Report button uses, so the headline can never drift from the
exported report. Hidden by default; shown on completion; cleared
on next Start.

### Files touched

* `widgets/rdma_blast_flow_dialog.py` — `_results_card` QLabel,
  `_render_results_card()`, hide-on-Start, hook in
  `_on_both_finished`
* `widgets/rdma_topology_dialog.py` — same wiring; topology card
  surfaces pair count + shape in addition to Blast's fields
* `tests/test_v05165_results_card.py` — 10 source-level
  assertions covering widget presence, HTML escape, hide-on-
  Start, and topology-specific extras

## [0.5.164] - 2026-06-16

**Live progress visualization while perftest is running.**

Operator: "see the visualization during test running" — Live
stats was just spamming ~270 identical "running" lines with no
indication of progress.

### What's added

Both dialogs grew a progress strip between the action row and the
Live stats / Results panel:
* `QProgressBar` (0-100%) — bar shows `elapsed / duration × 100`
* `QLabel` heading with iteration context and the raw seconds:
  * Blast: `Running • 247s / 600s` or
    `Iteration 2/5 • 247s / 600s` for iterate-N runs
  * Topology: `2 pair(s) running • 247s / 600s` or
    `Iteration 2/5 • 3 pair(s) • 247s / 600s`

### How it works

Both dialogs already polled every 2 s and got back the perftest
job's `started_at` timestamp. The new `_update_progress_widget`
(Blast) / `_update_progress_from_jobs` (Topology) helpers
compute elapsed against the operator-set Duration and push to
the bar. Hidden by default; shown on first running-poll;
hidden again when the run settles (`_on_both_finished` /
`_all_pairs_done`) or the operator hits Stop.

### Tests

`tests/test_v05164_live_progress.py` — 5 new (progress widget
declared in both dialogs, update helper present, reset on
finish/stop, hidden by default). Combined RDMA regression
sweep: 183 passing.

## [0.5.163] - 2026-06-16

**HTML report export in both Blast and Topology dialogs.**

Operator: "also allow user to generate report for this test both
in via blast test and topology test". Confirmed format = HTML,
scope = all runs since the dialog opened.

### Shared builder

`utils/rdma_report.py` — pure function `build_html_report(title,
runs, generated_at, client_version=None)` that renders a self-
contained HTML doc with inline CSS. No external assets, no JS.
Operators can email / archive the file as-is.

### Per-dialog wiring

Both dialogs gained:
* `_run_log: List[Dict]` — append-only, reset on dialog construction
* `📄 Export report…` button next to Pre-flight check
* `_append_run_log_entry()` called after every Start cycle
  completes (single iteration OR the full iterate-N session) —
  captures params, endpoints, per-iteration / per-worker /
  per-pair rows, and the Σ summary.
* `_on_export_report_clicked()` — Save-As dialog with a
  timestamped default filename
  (`netgen-blast-report-YYYYMMDD_HHMMSS.html` /
  `netgen-topology-report-...`), then writes the rendered HTML.

### Report contents

For each run:
* Title + per-run kind pill (blast/topology) + test ID
* Started-at timestamp
* Parameters table (msg_size, qp_count, mtu, duration, parallel
  workers, iterations, bidirectional, cpu_util, ...)
* Endpoints (server/client for Blast; pair list for Topology)
* Results table: per-iteration or per-pair rows + Σ summary row
  (avg/min/max BW + avg MsgRate). Lat tests get a separate
  layout (avg / p99 columns).

### Tests

`tests/test_v05163_html_report.py` — 6 new (builder smoke,
empty-runs path, BW + Lat rendering, HTML-escape safety, both
dialogs grew the export button + run log). Combined RDMA
regression sweep: 178 passing.

## [0.5.162] - 2026-06-16

**CRITICAL: --cpu_util adds a 6th column; parser was anchored
to 5. Enabling "CPU util" made every BW run report None across
the board (rc=0 but no parsed BW / iters / msg_rate).**

Operator screenshot showed exactly the symptom: `[server] done
(rc=0) size=NoneB iters=None BW avg=None ... peak=None MsgRate=
None Mpps` for both halves.

perftest BW data row WITHOUT `--cpu_util`:
  `65536  5244904  171.55  171.21  0.327198`
   bytes  iters    peak    avg     mrate

perftest BW data row WITH `--cpu_util`:
  `65536  5244904  171.55  171.21  0.327198  12.34`
   bytes  iters    peak    avg     mrate     cpu%

`_RE_BW_DATA_ROW` ended with `(?P<mrate>[\\d.]+)\\s*$` — anchored
to end-of-line. The trailing CPU util column made the regex miss
the row entirely; final_* fields stayed None.

Fix: make the 6th column optional on both BW and Lat regexes
(`(?:\\s+(?P<cpu_util>[\\d.]+))?\\s*$`). Lat tests use `--cpu_util`
identically — perftest appends a column there too.

### Tests

`tests/test_v05162_cpu_util_parser.py` — 4 new (BW with/without
cpu_util, Lat with/without). Combined RDMA regression sweep:
172 passing.

## [0.5.161] - 2026-06-16

**CRITICAL: extras' perftest client never knew which port to
dial — instant rc=1 on every multi-worker run.**

Operator screenshot from 16-worker BW run: worker 0 finished at
171 Gbps, all 15 extra clients reported `done (rc=1) BW avg=None`
within ~2 seconds of "both halves running". Both dialogs affected.

### Blast: extras client body never set `listen_port`

`_on_extra_server_started` POSTed the server start, got back a
job_id + listen_port in the response, and then built the client
body WITHOUT copying the listen_port through. The server bound
to whatever port `_allocate_port()` picked; the client's
perftest called `_allocate_port()` again and got a DIFFERENT
random port; perftest's `-p` arg disagreed; client couldn't
reach server; exit rc=1.

Worker 0's path has done this correctly since v0.4.0
(`_on_server_started` → `listen_port: listen_port` in client
body). The v0.5.155 extras-fan-out code just forgot.

Fix: extract `data.get("listen_port")` from the server response
and pass it as `listen_port` in the client body. Mirror worker 0.

### Topology: wrong key name (`peer_port` instead of `listen_port`)

`_on_extra_srv` built the client body with `"peer_port": _port`
— but the server's `/api/rdma/perftest/start` route has NO
`peer_port` key (only `peer_addr` + `listen_port`). The client's
perftest got no port hint; `_allocate_port()` picked a random
port; same failure mode as Blast above.

Fix: rename `peer_port` → `listen_port`. The server already binds
to `extra_port` via `"listen_port": extra_port`; the client now
connects to the same port.

### Test asserts

Added `tests/test_v05161_extras_listen_port.py` (3 tests) that
greps both dialogs' `_start_extra_workers` / `_start_pair_extra_
workers` for `listen_port` in the client-body construction, and
confirms `peer_port` is gone from the Topology body. Combined RDMA
regression sweep: 168 passing.

## [0.5.160] - 2026-06-15

**Topology dialog UI polish + N-run iteration loop.**

Triggered by operator's Topology Test screenshot ("UI polish is
needed for RDMA topology test also, Shared workload input section
can be more compact, increase stats section vertical size, add
number of iterations to run in the shared workload section so that
when user start topology test it iterate through and record the
results in the per pair stats section #0, #1.. etc.").

NOT YET RELEASED — bump + commit only, awaiting operator approval.

### UI polish

* Shared workload grid: vertical spacing 8 → 4 (operator wanted
  "more compact"; the v0.5.159 bump to 8 was for Blast's busier
  layout). Margins tightened to (8, 4, 8, 4).
* Per-pair stats table: `setMinimumHeight` 160 → 360. With
  Iterations × pairs the table can grow long; the bigger min
  height makes the full run visible without resizing.

### Iterations feature

* New "Iterations" spinbox in Shared workload (range 1–1000,
  default 1). Tooltip explains the variance-characterization use
  case (RoCE BW can swing 5–10% between runs).
* `_proceed_with_topology_start` now sets up the iteration loop
  state and dispatches to a new `_run_one_iteration()` method.
  Each iteration:
  * Resets `_pair_jobs` and `_latest_jobs` so the next iteration's
    polling doesn't pick up stale job_ids.
  * Captures the current row offset as `_current_iter_base_row`.
  * Appends one row per pair to the table labeled `#<iter>.<pair>`
    (single-iteration mode keeps the old `<pair>` label).
  * Fires server-side perftest starts; client-side starts chain
    in the response callbacks (existing flow).
* `_update_pair_row(pair_index)` writes to row
  `_current_iter_base_row + pair_index` — previous iterations'
  rows are never overwritten.
* After `_all_pairs_done()`, `_on_job_resp` snapshots the
  iteration's per-pair stats, bumps `_iteration_idx`, and either
  schedules `_run_one_iteration()` via `QTimer.singleShot(500)`
  for the next iteration or emits a Σ summary row when all done.
* `_append_summary_row()` adds an "Σ" row showing avg/min/max BW
  and MsgRate across all (iter, pair) samples. Skipped when
  Iterations = 1 (single row would just be duplicated).
* `_on_stop_clicked` sets `_stop_requested = True` so the
  iteration loop halts after the current iteration's pairs
  finish.

### Tests

`tests/test_v05160_topology_iterations.py` — 14 new tests covering
the spinbox, iteration state setup, per-iteration row offset, row
labeling, snapshot capture, summary row math, and Stop semantics.
v0.5.159 test updated for the v0.5.160 spacing revert. Combined
RDMA regression sweep: 156 passing.

## [0.5.159] - 2026-06-15

**Critical extras-unwrap fix + v0.5.157 regression revert + UI
polish.**

Triggered by operator's 16-worker Blast run on srv06 that showed:
worker 0 finished at 156 Gbps, zero per-worker done lines for
extras 1-15, no TOTAL line, and the v0.5.158 "no host_info cached"
warning firing even though 🚀 Max BW had been clicked.

NOT YET RELEASED — bump + commit only, awaiting operator approval.

### #1 — CRITICAL: extras never marked finished (since v0.5.155)

`_on_extra_job_resp` read `data.get("finished_at")` directly. But
`/api/rdma/perftest/job/<id>` wraps the job in `{"job": {...}}` —
`_on_job_resp` (worker 0) correctly unwraps it; the extras path
never did. So every extra worker sat in `_extra_workers` forever
with `server_finished=False, client_finished=False`,
`_maybe_emit_total` bailed at the "every extra done?" check, and
per-worker done rows were silently swallowed. Bug present since
v0.5.155's parallel-worker introduction.

Fix: unwrap `data["job"]` and read `final_bw_avg_gbps` /
`final_msg_rate_mpps` / `returncode` / `finished_at` from there.

### #2 — REGRESSION: v0.5.157 wiped `_host_info_cache` on Start

v0.5.157 added `self._host_info_cache = None` (Blast) and
`= {}` (Topology) in `_proceed_with_*_start` and `closeEvent`.
The reasoning was "HCA change between Starts kept previous HCA's
NUMA snapshot." That reasoning was wrong — `host_info` is a
HOST-LEVEL fact (NUMA topology + full `hca_numa` map for every
HCA on the box, not just the currently-selected one). Wiping it
silently discarded the operator's 🚀 Max BW click; the next Start
fell back to `list(range(N))` with no `numa_pin`, and v0.5.158's
"(operator skipped 🚀 Max BW)" warning fired even when they
hadn't.

Fix: drop all four cache resets. The cache is fine to reuse —
operator clicks 🚀 again for fresh info.

### #3 — LATENT: v0.5.158 AttributeError in Topology fallback

v0.5.158 added `self._stats_view.append(...)` to Topology's
`_start_pair_extra_workers` to surface the NUMA-blind fallback.
But Topology has `_stats_table` + `_status_label`, not
`_stats_view` — that path would AttributeError if hit. Redirected
to `_set_status_error` (which writes the operator-visible status
label).

### #4 — UI polish (operator screenshot)

* `Verify` button widened: `setFixedWidth(78)` → `setMinimumWidth
  (96)`. Was clipping "Verify" on macOS Big Sur+.
* `🚀 Max BW` button widened: 86/82 px → `setMinimumWidth(108)`
  in both dialogs. Was clipping "Max BW" on macOS.
* Test parameters grid vertical spacing: 2 → 8 (Blast),
  4 → 8 (Topology). Spinbox baselines were kissing on Retina.
* Live stats `setMinimumHeight`: 280 → 320. With 16 workers the
  "[worker N] both halves running" lines pushed the `[client]
  done` row below the visible area.
* Live stats auto-scroll: connect `textChanged` →
  `_scroll_stats_to_bottom`. Default Qt only auto-scrolls when
  the bar is already at max; after ~270 running-tick lines, the
  `[client] done` summary was clipped at the bottom.

### Tests

`tests/test_v05159_extras_unwrap_and_polish.py` — 14 new tests.
v0.5.152, v0.5.157, v0.5.158 tests updated to match the
intentional reverts. Combined RDMA regression sweep: 143 passing.

## [0.5.158] - 2026-06-15

**Slice C: polish — dead-alias cleanup + fallback warning
surface.**

Operator: "go A first then B, and then C" — final slice.

### #1 — Dead `_SameSubnetTrapConfirmDialog` alias removed

v0.5.153 renamed the v0.5.152 class to `_StartBlockerConfirmDialog`
and kept the old name as `_SameSubnetTrapConfirmDialog =
_StartBlockerConfirmDialog` for back-compat. Nothing in the
codebase imported the old name; the alias just confused new
readers. Dropped. Tests updated to assert the alias is gone.

### #2 — Host-info fallback surfaced to operator

Before: when `_host_info_cache` was empty (operator didn't click
🚀 Max BW) OR `pick_workers_for_hca` couldn't find the HCA in
the host's NUMA map, `_start_extra_workers` (Blast) and
`_start_pair_extra_workers` (Topology) silently fell back to
`list(range(N))` — no NUMA pin, cross-NUMA RAM access, aggregate
BW capped. Operator had no signal this was happening and would
chase phantom wire issues.

Now both code paths build a `fallback_reason` string and
`_stats_view.append(f"[workers] ⚠ {reason}")` (Blast) or
`[pair #N] ⚠ {reason}` (Topology). Three concrete reasons:

* `no host_info cached (operator skipped 🚀 Max BW) — linear CPU
  ordering, no NUMA pin. Cross-NUMA RAM access may cap aggregate
  BW.`
* `HCA <name> not in host's NUMA map — linear CPU ordering, no
  NUMA pin`
* `pick_workers_for_hca failed: <exc>`

### Tests

`tests/test_v05158_slice_c_polish.py` — 5 new. Combined RDMA
regression sweep with Slice C: 129 passing.

## [0.5.157] - 2026-06-15

**Slice B: Blast + Topology multi-worker hygiene.**

Operator: "go A first then B, and then C" — this is slice B,
closing the four bugs the v0.5.156 ship surfaced in the parallel-
worker code paths.

### #1 — `_host_info_cache` reset on every Start

Before: the cache was populated once (by the 🚀 Max BW button)
and lived for the lifetime of the dialog. Choosing a different
HCA between Starts kept the previous HCA's NUMA snapshot —
workers got pinned to the wrong NUMA node, hitting the cross-
NUMA penalty we fixed in v0.5.131.

Now both `_proceed_with_start` (Blast) and
`_proceed_with_topology_start` (Topology) drop the cache on
every Start. The next 🚀 Max BW click re-queries `/api/rdma/
host_info` against the current HCA.

### #2 — TOTAL line includes worker 0

Before: `_maybe_emit_total` only summed extras 1..N-1 and printed
"extras only — worker 0 line above is separate". Operator had to
add the "[client] done ... BW=X" row to the "[TOTAL extras only]"
row by hand.

Now: `_on_job_resp` captures worker 0's `final_bw_avg_gbps` and
`final_msg_rate_mpps` into `_client_bw` / `_client_msgrate` when
the client side finishes. `_maybe_emit_total` adds those to the
extras sum and emits one canonical
`[TOTAL across N worker(s)] BW=X Gbps  MsgRate=Y Mpps`.

Single-worker runs also benefit: TOTAL now fires after worker 0
finishes even when `_extra_workers` is empty.

### #3 — `closeEvent` resets multi-worker state

Before: closing the Blast dialog and reopening it kept stale
entries in `_extra_workers` (poll loop would tick against
dead job_ids), kept `_total_emitted=True` (suppressed the next
run's summary), and kept the previous HCA's `_host_info_cache`.

Now both `closeEvent` paths reset: `_extra_workers = []`,
`_total_emitted = False`, `_client_bw = None`,
`_client_msgrate = None`, `_host_info_cache = None`/`{}`.
Topology also clears `_pair_extra_workers`.

### #4 — `cpu_pin` clamped to `cpu_count - 1`

Before: when `host_info_cache` was missing OR
`pick_workers_for_hca` fell back to a linear range, the dialog
could pass `cpu_pin=32` on a 16-core host. `taskset` rejects with
"Invalid argument" and the worker silently fails to start.

Now both `_start_extra_workers` (Blast) and
`_start_pair_extra_workers` (Topology) clamp every picked CPU to
`cpu_count - 1` whenever `host_info` has a cpu_count. Multiple
workers land on the same top CPU — perftest's per-QP isolation
still gives real parallelism even when the taskset arg collides.

### Tests

`tests/test_v05157_blast_multiworker_hygiene.py` — 11 new tests
covering every fix above. Combined RDMA regression sweep (Slice A,
v0.5.155, v0.5.153, v0.5.152, v0.5.150 + this slice): 124 passing.

## [0.5.156] - 2026-06-15

**Slice A: Topology dialog gains Blast's v0.5.152-155 quality
features (audit BUG #1 + #2).**

Operator: "go A first then B, and then C" (after the v0.5.155
audit).

The audit found two operator-blocking gaps in Topology vs Blast:

### #1 — Auto-detect on Start

Before: `_on_start_clicked` fired perftest immediately on every
pair. Same-host configurations hit the QP→RTR routing trap silently
with no recovery path.

Now:
* Reuses Blast's `_detect_start_blockers` +
  `_StartBlockerConfirmDialog` (imported from
  `widgets/rdma_blast_flow_dialog.py` — no logic duplication).
* On Start, if any pair has `server.tg_url == client.tg_url` AND
  no Pre-flight state was already applied → probe the FIRST
  same-host pair's two endpoints in parallel.
* If a blocker is detected (`probe_failed` / `down_port` /
  `missing_ip` / `same_subnet`) → pop the contextual confirm with
  Apply & Start / Continue / Cancel / Open Pre-flight.
* On Apply → POST `/validate` then `/configure` then proceed.
* Refactored: original per-pair start moved into
  `_proceed_with_topology_start(plans, spec)`.

### #2 — Parallel workers + 🚀 Max BW

Before: each pair ran 1 perftest per side. No multi-core BW
scaling.

Now:
* **Parallel workers** spinbox (range 1–64, default 1) in the
  Shared workload group.
* **🚀 Max BW** button: queries the first server endpoint's
  `/api/rdma/host_info` → calls `pick_workers_for_hca` → divides
  by pair count → sets the spinbox.
* Per-pair fan-out: after each pair's worker 0 is up,
  `_start_pair_extra_workers(plan, N)` spawns workers 1..N-1 with:
  - Unique `cpu_pin` (next NUMA-local core),
  - Shared `numa_pin` (HCA's home node),
  - Unique `listen_port` (`plan.base_listen_port + worker_idx`),
  - Unique `handshake_id` (`<pair>-w<N>-<uuid6>` so netgen's
    handshake broker pairs the right server with the right client).
* `_pair_extra_workers: Dict[pair_index, List[worker]]` tracks
  extras per pair.

### Why divide by pair count

Topology can spawn many pairs (e.g., mesh shape). If each pair
spawned 12 workers, a 4-pair mesh × 12 = 48 workers per side =
96 perftest processes on one host. The picker divides NUMA-local
cores by `pair_count` so the total stays bounded.

### Files changed
- `widgets/rdma_topology_dialog.py` (~280 lines: auto-detect
  flow + Parallel workers UI + per-pair fan-out).
- `pyproject.toml` (0.5.155 → 0.5.156).
- `tests/test_v05156_topology_parity.py` (16 tests).

### Deferred (next ships)
- Slice B (v0.5.157): Blast multi-worker hygiene — `_host_info_cache`
  staleness, TOTAL line includes worker 0, `_extra_workers` cleanup
  on close, cpu_pin validation.
- Slice C (v0.5.158): Polish — remove dead alias, log on
  host_info fallback.

### Verified
```
$ ./venv/bin/pytest tests/test_v05156_topology_parity.py -q
16 passed in 0.07s

$ ./venv/bin/pytest tests/ -q -k "rdma or blast or topology or perftest or qp or preflight"
525 passed, 2455 deselected
```

---

## [0.5.155] - 2026-06-15

**Parallel workers + 🚀 Max BW: true single-HCA BW scaling via CPU
+ NUMA pinning.**

Operator: "how can we add parallel cpus for BW scale?" → "go max"
(Layer 2 + Layer 3 in one ship).

perftest is single-threaded — single-process QP scaling tops out
at one CPU's worth of work (~171 Gbps on srv06's 200 GbE). v0.5.155
spawns N independent perftest processes per side, each pinned to a
different core. Pinning all workers to the HCA's home NUMA node
aligns CPU + RAM + PCIe (avoids the cross-NUMA penalty from
v0.5.131).

### Server side

* **`start_perftest()` accepts `cpu_pin` + `numa_pin`** in `opts`.
  When set, the perftest spawn is wrapped in
  `numactl --cpunodebind=<N> --membind=<N> -- taskset -c <N>
  <perftest> …`.
* **New `utils/rdma_host_info.py`**:
  - `host_info()` → `{cpu_count, numa_nodes, hca_numa}` snapshot
    from sysfs (`/sys/devices/system/node/`,
    `/sys/class/infiniband/<hca>/device/numa_node`).
  - `pick_workers_for_hca(hca, requested?, info?)` →
    `{worker_count, numa_pin, cpus, reason}`. Picks NUMA-local
    cores; caps at 16 (past that, marginal BW drops and HCA QP
    context cache thrashes).
  - Synthesizes a flat `node 0` when sysfs is absent
    (containers, dev machines).
* **New `GET /api/rdma/host_info`** route. Returns the safe
  `{cpu_count:1, numa_nodes:[], hca_numa:{}}` skeleton on any
  internal failure — never raises.

### Client side

* **"Parallel workers"** spinbox in the Test parameters grid
  (range 1–64, default 1).
* **"🚀 Max BW"** button next to it. Click → `GET host_info` →
  `pick_workers_for_hca(server_hca)` → spinbox auto-fills with
  the NUMA-local CPU count. Status banner shows the rationale
  ("12 worker(s) pinned to NUMA node 0 (HCA mlx5_0's home node).").
* **Worker fan-out**: when Parallel workers > 1, after worker 0
  (the existing `_server_job_id`/`_client_job_id`) is up,
  `_start_extra_workers()` spawns workers 1..N-1 — each with:
  - Unique `cpu_pin` (next NUMA-local core),
  - Shared `numa_pin` (the HCA's home node),
  - Unique `listen_port` (`base_port + worker_idx`),
  - Unique `handshake_id` (`<base>-w<N>` so netgen's handshake
    broker doesn't cross-wire workers).
* **Stop / Close** tear down all workers (worker 0 +
  `_extra_workers`).
* **Poll loop** iterates worker 0 + extras.
* **Live stats**: per-worker `[worker N/server|client] done
  (rc=0)  BW avg=… Gbps  MsgRate=… Mpps` lines. Once every
  worker half is done, an inline **TOTAL** line sums BW + MsgRate
  across extras.

### What you'd see on srv06

Click "🚀 Max BW" with `rocep43s0f0` selected:
- Spinbox auto-fills to (e.g.) 12 if the HCA is on a 12-core
  NUMA node.
- Click Start → 12 perftest pairs spawn, each on a distinct
  core, all NUMA-local.
- Expected total BW: ~10× single-worker (with diminishing
  returns above ~10 workers due to PCIe-Gen5 x16 ceiling at
  ~500 Gbps practical). The 200 Gbps wire is your real cap
  long before CPU is.

### Files changed
- `utils/rdma_perf.py` (~30 lines: cpu_pin + numa_pin wrappers).
- `utils/rdma_host_info.py` (NEW, ~220 lines).
- `run_tgen_server.py` (+~25 lines: `/api/rdma/host_info`).
- `widgets/rdma_blast_flow_dialog.py` (+~250 lines: UI controls,
  `_on_max_bw_clicked`, `_start_extra_workers`, `_poll_extras`,
  `_on_extra_job_resp`, `_maybe_emit_total`, Stop iteration).
- `pyproject.toml` (0.5.154 → 0.5.155).
- `tests/test_v05155_parallel_workers.py` (25 tests).

### Deferred
* **Aggregate worker 0 into the TOTAL line** — currently the
  TOTAL summary only sums extras (worker 0's stats appear in
  the existing single-worker output). v0.5.156 if you want a
  single Grand Total line.
* **Topology dialog** parallel-worker mode — same fan-out shape
  but per-pair. Not in v0.5.155 because Topology already supports
  multi-pair via its endpoint editors, which gives similar
  scaling at the cost of typing more endpoint lines.

### Verified
```
$ ./venv/bin/pytest tests/test_v05155_parallel_workers.py -q
25 passed in 0.08s

$ ./venv/bin/pytest tests/ -q -k "rdma or blast or topology or perftest or qp or preflight"
509 passed, 2455 deselected
```

---

## [0.5.154] - 2026-06-15

**Pre-flight Test CIDR + Notes are now inline in the Endpoint
probe table.**

Operator: "allow user to modify ips in the Endpoint probe table
itself insted of seprate temp ip config section."

v0.5.150-v0.5.153 had two parallel views of the same endpoint:
the read-only **Endpoint probes** table on top and the editable
**Temporary IP configuration** grid below. They duplicated the
iface name and required the operator to look in two places to
read state vs propose a fix.

v0.5.154 folds them into one table.

### What changed

* Probe table columns: 7 → 9. New columns at the right edge:
  - **Test CIDR** (column 7) — a QLineEdit per iface, populated
    with the auto-suggested non-conflicting /24 (empty if the
    iface already has an IPv4; placeholder "(leave empty to
    skip)").
  - **Notes** (column 8) — a QLabel per iface that shows
    auto-suggest notes ("already has IPv4 (X); leave empty to
    skip") and inline validation results after Validate/Apply.
* The **IPs** column is renamed **Existing IPs** so the operator
  sees existing-vs-proposed side by side on the same row.
* The standalone **"Temporary IP configuration"** GroupBox is
  gone. Its rp_filter checkbox, Validate / Apply / Cleanup
  buttons, and 📌 Keep checkbox now sit directly below the
  verdict banner — same widgets, less chrome.
* The validation-failure border styling (red/amber) is rewritten
  with the `QLineEdit { … }` selector so it actually paints when
  the widget lives inside a QTableWidget cell. (The old bare
  `border: …` rule didn't always apply through the cell-widget
  proxy.)

Net: one row per endpoint = one mental model. Less scrolling,
fewer columns of iface labels, cleaner failure surface.

### Files changed
- `widgets/rdma_preflight_dialog.py` (~150 lines).
- `pyproject.toml` (0.5.153 → 0.5.154).
- `tests/test_v05154_preflight_inline_cidr.py` (14 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05154_preflight_inline_cidr.py -q
14 passed in 0.06s

$ ./venv/bin/pytest tests/ -q -k "rdma or blast or topology or preflight or qp or perftest"
507 passed, 2432 deselected
```

---

## [0.5.153] - 2026-06-15

**Blast RDMA Flow: consistency bug sweep.**

Operator: "check all the bugs in Blast RDMA flows, seems there are
some inconsistancy issue what error is reporting and what is
configured."

An Explore-agent audit found 8 inconsistencies between configured
state and reported state. v0.5.153 ships the top 6 operator-
blocking + resource-leak fixes.

### #1 — Pre-flight auto-suggest stops creating the trap

Was hardcoding `10.42.0.1/24 + 10.42.0.2/24` for the 2-iface case
— same subnet, the literal trap pre-flight exists to prevent.
Validator caught it on Validate (operator screenshot), but the
dialog shouldn't have proposed it.

The rewritten `_populate_config_rows()`:
- Walks every probe's existing IPv4 subnets, collects them in
  `occupied`.
- For each iface that doesn't have an IPv4, picks the next
  `10.N.0.0/24` not in `occupied` AND not already proposed
  to any sibling iface.
- Skips ifaces that already have an IPv4 — those don't need a
  test CIDR; the box stays empty with a clarifying note
  ("already has IPv4 (10.43.0.2/24); leave empty to skip").
- Each empty CIDR box gets a placeholder ("(leave empty to
  skip)") so empty ≠ broken.

### #2 — Pre-flight "Pre-flight OK" verdict + Test CIDR section consistency

When the verdict is green, the Test CIDR fields are empty AND
the status line says "All endpoints already have IPv4 addresses
in non-conflicting subnets. Apply is only needed if you want to
add additional test IPs." No more "OK but here's auto-fill that
fails Validate" contradiction.

### #3 — Auto-apply-on-Start validates before configure

Was POSTing `/configure` directly. If `10.43.0.0/24` was already
a kernel route, the apply silently failed AFTER the operator
committed to Start. Now `_apply_test_ips_then_start()`:
1. POST `/api/rdma/test_ifaces/validate` with the picked CIDRs.
2. If any issue has `severity: "error"` → abort with the
   first error message, point at Pre-flight.
3. Only on validate-ok → POST `/configure` → on success →
   proceed with perftest start.

### #4 — Start-probe blocker detection is now comprehensive

`_detect_same_subnet_trap()` only caught subnet collisions.
DOWN ports and missing IPs returned False → Start fired → perftest
died with "QP→RTR" → operator saw same error string with the
wrong root cause.

New `_detect_start_blockers()` catches four reason codes in
priority order:
- `probe_failed` — either probe errored (timeout, server down).
- `down_port`    — at least one port is not ACTIVE.
- `missing_ip`   — at least one iface has no IPv4.
- `same_subnet`  — both ifaces in one subnet on same host.

### #5 — Confirm dialog is now contextual

Renamed `_SameSubnetTrapConfirmDialog` →
`_StartBlockerConfirmDialog` (old name kept as alias). Builds
its title, body text, and button set per reason:
- Auto-fixable reasons (`missing_ip`, `same_subnet`) offer
  **Apply & Start** / **Continue anyway** / **Cancel**.
- Non-fixable reasons (`down_port`, `probe_failed`) offer
  **Open Pre-flight…** / **Continue anyway** / **Cancel** —
  netgen can't bring a link up or revive a dead server, so
  routing the operator into Pre-flight for manual investigation
  is the right affordance.

The new "Open Pre-flight" choice on the confirm dialog routes
the operator's flow into the Pre-flight panel without aborting
the test setup entirely.

### #6 — Stop button fires preflight cleanup

`_on_stop_clicked()` now invokes `_cleanup_preflight_state_ids()`
(same code path as `closeEvent`). Apply IPs → Start → Stop now
leaves the wire clean instead of waiting for dialog close.

### Files changed
- `widgets/rdma_blast_flow_dialog.py` (~150 lines: new
  `_detect_start_blockers`, generalized `_StartBlockerConfirmDialog`,
  validate-then-configure auto-apply, Stop cleanup).
- `widgets/rdma_preflight_dialog.py` (~70 lines: smart
  CIDR suggester + idle status banner + placeholder).
- `pyproject.toml` (0.5.152 → 0.5.153).
- `tests/test_v05153_blast_consistency_sweep.py` (19 tests).
- `tests/test_v05152_blast_compact_and_autotrap.py` (4 tests
  updated for the rename).

### Deferred to v0.5.154
Audit also flagged BUG #4 (Keep checkbox needs a manual cleanup
affordance on the Blast dialog), BUG #6 (status-label staleness
on rapid close), and BUG #8 (Loopback port-count clamp). Plus
the DOWN-port color-coding in the device picker combo.

### Verified
```
$ ./venv/bin/pytest tests/test_v05153_blast_consistency_sweep.py -q
19 passed in 0.08s

$ ./venv/bin/pytest tests/ -q -k "rdma or blast or topology or perftest or qp"
403 passed, 2522 deselected
```

---

## [0.5.152] - 2026-06-15

**Blast RDMA dialog: compact params, taller stats, auto-trap-
detect on Start, Keep-IPs option.**

Operator (after the same-subnet trap returned because v0.5.150's
auto-cleanup ran on the previous dialog close):

> "option C, and also make test paramter section compact and
> increase Live stats log vertical area"

### UI compaction

* Test-params grid: `setVerticalSpacing(2)` (was 4),
  `setContentsMargins(6, 2, 6, 2)` (was 8, 4, 8, 4).
* Spinboxes shrunk from 120 px → 100 px (5 of them: msg_size,
  tx_depth, qp_count, gid_index, duration).
* Live stats `setMinimumHeight(280)` (was 160), added stretch
  factor 1 on `addWidget` so it claims the freed vertical room.

Net effect: ~80 px reclaimed for the Live stats panel.

### Auto-detect-on-Start (Option C-B)

Click Start. If the server + client TGs are the same host AND no
preflight test IPs are already in play, the dialog probes both
endpoints via `/api/rdma/probe` (~200 ms). If both kernel ifaces
share an IPv4 subnet → pop `_SameSubnetTrapConfirmDialog` with
three buttons:

* **Apply & Start** (default) — auto-picks `10.42.0.1/24` +
  `10.43.0.1/24`, POSTs `/api/rdma/test_ifaces/configure`,
  tracks the state_id, then proceeds with perftest start.
* **Continue anyway** — operator overrides; perftest will
  almost certainly fail at QP→RTR, but they get to see it.
* **Cancel** — abort.

Probe runs in parallel for both endpoints. Skipped entirely
when:
- TGs are different hosts (trap is impossible across hosts).
- Operator already applied test IPs via Pre-flight this
  session.

Pure helper `_detect_same_subnet_trap(srv_probe, cli_probe)` at
module level — testable without Qt. Skips IPv6 (different trap
shape).

### 📌 Keep-IPs checkbox (Option C-A)

Pre-flight dialog grew a **"📌 Keep these test IPs after this
dialog closes"** checkbox under the Apply/Cleanup buttons. When
checked, `RdmaPreflightDialog.keep_applied()` returns True; the
parent (Blast or Topology) skips adding the state_id to the
auto-cleanup set. Operator manages cleanup explicitly via the
dialog's button, `POST /api/rdma/test_ifaces/cleanup` with
`state_id=null`, or reboot.

Both Blast and Topology dialogs honor this signal.

### Diagnostic flow for the srv06 operator (now)

1. Open Blast a RDMA Flow.
2. Pick `rocep43s0f0` server / `rocep43s0f1` client (one click
   each thanks to v0.5.147 mirror / v0.5.149 OTHER-HCA).
3. Click **Start**.
4. Same-subnet trap detected automatically → confirm dialog
   pops → click **Apply & Start**.
5. Test IPs applied, perftest fires, 171 Gbps result row.
6. Iterate on test params (msg_size, qp_count) and Start
   again — second run skips the probe (state_id is already
   tracked).
7. Close the Blast dialog → cleanup fires automatically.

If you want IPs to persist across multiple dialog opens:
- Pre-flight → ✅ 📌 Keep checkbox before clicking Apply.
- Cleanup is now your responsibility.

### Files changed
- `widgets/rdma_blast_flow_dialog.py` (compact UI, taller
  stats, `_detect_same_subnet_trap`, `_auto_probe_then_start`,
  `_apply_test_ips_then_start`, `_proceed_with_start`,
  `_SameSubnetTrapConfirmDialog`, keep_applied honor).
- `widgets/rdma_preflight_dialog.py` (Keep checkbox +
  `keep_applied()` method).
- `widgets/rdma_topology_dialog.py` (keep_applied honor).
- `pyproject.toml` (0.5.151 → 0.5.152).
- `tests/test_v05152_blast_compact_and_autotrap.py` (23 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05152_blast_compact_and_autotrap.py -q
23 passed in 0.11s

$ ./venv/bin/pytest tests/ -q -k "rdma or blast or topology or qp or perftest"
384 passed, 2522 deselected
```

---

## [0.5.151] - 2026-06-15

**Blast RDMA Flow dialog: in-place QP-verify help.**

Operator (after successfully running 171 Gbps with `qp_count=10`):

> "increased QP=10, how do i verify what QPs being used to send
> roce traffic ?"

perftest doesn't print its QP inventory by default. The answer
is `rdma resource show qp link <hca>/<port>`, but the operator
shouldn't have to remember the syntax, look up the HCA name,
and re-type the IB port — all three are already on the dialog.

### What's new

* **"❓ Verify" button** next to the QP-count spinbox.
* Click → opens new `_QpVerifyHelpDialog` with five sections, each
  containing copy-paste-ready SSH-wrapped commands pre-filled
  with the operator's current dialog state:

  1. **Count** — `rdma resource show qp link <hca>/<port> | wc -l`
     ≈ qp_count + 1 control QP.
  2. **Detail** — `… -d` (state, PD, pid). Look for state `RTS`
     on every QP; `INIT`/`RTR` means the QP is stuck.
  3. **JSON** — `… -jp` for scripting / `jq` piping.
  4. **perftest verbose** — same job re-run via the `perf_extra`
     escape hatch with `["-v"]`. Verbose stdout shows every
     QP's QPN as it's created.
  5. **Wire cross-check** — `ethtool -S … | grep tx_packets_phy`
     sampled twice → pps. Should match the result row's MsgRate.

* Per-command **Copy** buttons + a **Copy all commands** button
  for one-shot SSH sessions.
* Loopback-aware: when server and client share the same (host,
  HCA), the duplicate client command is suppressed.

### Why it matters

netgen had a button-driven test runner (Start) and a
button-driven test-IP applier (v0.5.150 Pre-flight) but
verification was still SSH-only. v0.5.151 closes that loop —
the operator can now design, run, AND verify entirely from the
Blast dialog.

### Files changed
- `widgets/rdma_blast_flow_dialog.py` (+QP-row button +
  `_show_qp_verify_help` + `_QpVerifyHelpDialog` class).
- `pyproject.toml` (0.5.150 → 0.5.151).
- `tests/test_v05151_qp_verify_help.py` (15 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05151_qp_verify_help.py -q
15 passed in 0.05s

$ ./venv/bin/pytest tests/ -q -k "blast or rdma or qp"
322 passed, 2561 deselected
```

### Future
Same affordance could land on the Topology dialog (per-pair QP
inventory). Holding off until you ask — Topology is N×M so the
help dialog needs to enumerate every pair's HCA, which is a
different UX.

---

## [0.5.150] - 2026-06-15

**RDMA Pre-flight check + user-controllable temporary test IPs.**

Operator: "go and also provide user flexibility to select the ip
address and check correct subnet configured by user."

Closes the deferred gap from v0.5.149. The earlier "Failed to
modify QP to RTR" failure on `rocep43s0f0 ↔ rocep43s0f1` was
the classic same-host same-subnet routing trap — both kernel
ifaces in one subnet → Linux routes via `lo` → RoCEv2 packets
never cross the wire. Operators had to drop to SSH and run
`ip addr add … && sysctl … && ip route add …`. v0.5.150 makes
this a button click with full validation.

### Server side

* **`utils/rdma_test_ifaces.py`** — new pure-helpers module:
  - `probe_device(hca)` → port state, link layer, kernel iface,
    IPs, rp_filter, RoCEv1/v2 GIDs.
  - `auto_pick_subnets()` — suggest non-conflicting RFC 1918
    /24s by scanning the routing table.
  - `validate_user_ips(ifaces)` — catches bad CIDR, missing
    iface, **same-subnet trap on the same host**, existing-
    route overlaps. Warnings vs hard errors.
  - `apply_test_config()` — `ip addr add … label <iface>:ng`,
    optional `sysctl rp_filter=0`, records state for cleanup.
  - `cleanup_test_config(state_id)` — restores exactly what we
    added; idempotent.
  - `find_orphan_test_ips()` — crash-recovery: scans `ip -br
    addr` for `<iface>:ng` labels not in our state.
  - State file: `/etc/netgen/rdma-test-ifaces.json` (atomic
    tmp+os.replace, same convention as the DPDK bind registry).
  - Label suffix `:ng` (2 chars) fits within Linux's IFNAMSIZ
    label cap for every modern predictable iface name.
* **5 new routes** in `run_tgen_server.py`:
  - `GET  /api/rdma/probe?device=<hca>&port=<n>`
  - `POST /api/rdma/test_ifaces/validate`
  - `POST /api/rdma/test_ifaces/configure`  (always validates
    first, refuses on hard errors)
  - `POST /api/rdma/test_ifaces/cleanup`  (specific state_id or
    omit for blast-radius reset)
  - `GET  /api/rdma/test_ifaces/orphans`  (crash recovery)
  - `device` query param sanitized against path traversal
    before any sysfs read.

### Client side

* **`widgets/rdma_preflight_dialog.py`** — new
  `RdmaPreflightDialog`:
  - Per-endpoint probe table (port state colored green/red, RoCEv2
    GIDs, IPs).
  - Verdict banner:
    - 🔴 **BLOCKER** when any port is DOWN.
    - 🟠 **Same-subnet trap detected** with the fix in plain
      English.
    - 🟠 IP missing when GIDs can't form.
    - 🟢 Pre-flight OK.
  - **User-editable CIDR field** per iface with auto-suggested
    value (different /24 per side so loopback trap doesn't get
    re-introduced).
  - **"Validate" button** — checks the operator's CIDRs WITHOUT
    applying. Surfaces errors inline next to each row.
  - **"Apply (temporary)" button** — POSTs configure (which
    validates again server-side, refuses on error). Marks each
    row applied in green; status banner shows the `state_id`.
  - **"Clean up applied" button** — undoes the last apply.
  - **rp_filter checkbox** (default on) — relaxes reverse-path
    filtering on the test ifaces, restored on cleanup.

### Wiring

* **"🔍 Pre-flight check"** button in the action row of BOTH
  Blast a RDMA Flow and RDMA Topology Test dialogs.
* Applied `state_id`s are tracked per-TG-URL on each parent
  dialog; **`closeEvent` fires cleanup automatically** (fire-
  and-forget POST) so test IPs never outlive the test.

### Diagnostic flow for the srv06 operator

1. Open RDMA Topology Test, pick `rocep43s0f0 ↔ rocep43s0f1`,
   click **🔍 Pre-flight check**.
2. Dialog shows port state ACTIVE on both, IPs in same subnet,
   verdict: **Same-subnet trap detected**.
3. Pre-populated CIDRs: `10.42.0.1/24` and `10.42.0.2/24` (or
   operator edits to whatever they want).
4. Click **Validate** → "Validation passed — safe to Apply."
5. Click **Apply (temporary)**. Server runs:
   `ip addr add 10.42.0.1/24 dev ens2f0np0 label ens2f0np0:ng`
   `ip addr add 10.42.0.2/24 dev ens2f1np1 label ens2f1np1:ng`
   `sysctl -w net.ipv4.conf.{f0,f1}.rp_filter=0`
6. Close preflight, run Start. perftest's QP→RTR transition
   now finds a working path. BW + msg-rate populate.
7. Close the Topology dialog → cleanup fires automatically,
   the test IPs vanish, rp_filter restored.
8. Reboot → no trace; nothing was persisted.

### Files changed
- `utils/rdma_test_ifaces.py` (new, ~370 lines).
- `widgets/rdma_preflight_dialog.py` (new, ~430 lines).
- `run_tgen_server.py` (+~110 lines: 5 new routes).
- `widgets/rdma_blast_flow_dialog.py` (+pre-flight button +
  state tracking + closeEvent cleanup).
- `widgets/rdma_topology_dialog.py` (same).
- `pyproject.toml` (0.5.149 → 0.5.150).
- `tests/test_v05150_rdma_preflight.py` (30 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05150_rdma_preflight.py -q
30 passed in 0.08s

$ ./venv/bin/pytest tests/ -q
2867 passed, 1 skipped in 83.28s
```

---

## [0.5.149] - 2026-06-15

**Blast RDMA Flow dialog — closes three v0.5.143-148 parity gaps.**

Operator: "check the gap in RDMA blast test and fix them."

An audit of `widgets/rdma_blast_flow_dialog.py` against the
recent Topology dialog improvements (v0.5.143 endpoint picker /
device clarity, v0.5.146 perftest error filter, v0.5.147 + 148
loopback / two-HCA buttons) surfaced three concrete gaps that
mattered to operator UX:

### A + F: error display no longer clips at 120 chars

**Before**: `chunk += f"  err={job.get('error')[:120]}"` clipped
the diagnostic the operator saw. v0.5.146's server-side
`_format_rc_error` already filters the perftest banner AND clips
its inner tail to ~400 chars — re-clipping to 120 chars on the
client meant any diagnostic past the first 120 chars was lost
even after the filter rescued it.

**After**: `chunk += f"\n  err={job.get('error')}"`. Multi-line
render through the existing `_stats_view` QTextEdit which
already wraps + scrolls. Operator sees the full cleaned message.

### C: "device = RDMA HCA" inline hint

**Before**: the device combos were labelled bare "Server device:"
/ "Client device:". Operators conflated the HCA name with the
Ethernet iface picker elsewhere in the GUI.

**After**: a small hint label under the device grid:
> **device** = RDMA HCA name (e.g. `mlx5_0`) — this is the
> InfiniBand verbs device, NOT an Ethernet interface
> (`ens2f0np0`). perftest addresses the HCA directly via
> libibverbs.

Same wording as the Topology dialog's v0.5.143 hint.

### B: "↔ Use OTHER HCA (same host two-port test)" button

**Before**: v0.5.147 added the same-HCA mirror button. The
two-HCA case (server=`rocep…f0`, client=`rocep…f1` for a dual-
port loopback or sibling-NIC test) still required manual combo
fiddling.

**After**: a second button next to the loopback mirror —
auto-picks the next available device on the client side. The
typical dual-port case becomes one click. Edge cases:
* Server combo still on `(probing…)` → no-op.
* Server device not yet on the client combo (asymmetric probe
  response) → falls back to the mirror so the operator at
  least gets a valid same-HCA loopback config.
* Only one real HCA on the client combo → no-op (two-HCA is
  meaningless with a single device).
* Skips placeholder entries (`(probing…)`, `(no HCAs)`) when
  walking the combo.

### Files changed
- `widgets/rdma_blast_flow_dialog.py` (HCA hint label, drop the
  `[:120]` clip, second `_other_hca_btn` + `_pick_other_hca_for_client`).
- `pyproject.toml` (0.5.148 → 0.5.149).
- `tests/test_v05149_blast_dialog_gaps.py` (12 tests pinning
  each gap fix).

### Deferred
Gap G from the audit — pre-flight `ibv_devinfo` PORT_DOWN / GID
mismatch check before Start — needs a server-side route, queued
for v0.5.150.

### Verified
```
$ ./venv/bin/pytest tests/test_v05149_blast_dialog_gaps.py -q
12 passed in 0.04s

$ ./venv/bin/pytest tests/ -q -k "rdma or perftest or topology or blast"
316 passed, 2522 deselected
```

---

## [0.5.148] - 2026-06-14

**Topology dialog's same-host picker gains "two HCAs same host"
mode.**

Operator after v0.5.147 shipped:

> "also allow same host different roce interfaces."

v0.5.147's Loopback button only handled the SAME-HCA case (both
sides bind to `mlx5_0`, verbs bounces internally). The operator's
real diagnostic interest is the dual-HCA case — `rocep43s0f0` ↔
`rocep43s0f1` on srv06 — which tests the wire/driver path between
sibling RoCE devices. That's the same test that hit "Failed to
modify QP to RTR" earlier; one-click setup helps operators
quickly verify whether the SAME issue persists after they fix
PFC / GID / port-state config.

### What's new

* The picker (now titled "Same-host RDMA Test — Pick HCA(s)")
  has a checkbox: **"Use a DIFFERENT HCA on the client side
  (same-host two-port test)"**.
* When checked, a second HCA combo enables for the client side.
  Auto-picks the NEXT device after the server's pick — common
  dual-port case (`rocep…f0` → `rocep…f1`) is one click.
* Switching the server-side HCA re-slides the client default so
  the two stay different by construction.
* OK button disables when both combos select the same device in
  two-HCA mode, with an inline hint asking the operator to pick
  different devices or uncheck the box.
* "(N HCAs) — Two-HCA mode needs at least 2" amber hint when the
  selected TG has only one HCA.
* Parent dialog button relabeled "↔ Same-host test (loopback or
  two HCAs)" with a tooltip explaining both modes.
* Picker API: `selected_line()` (single string) →
  `selected_lines()` returning `(server_line, client_line)`. In
  same-HCA mode both strings are identical; in two-HCA mode they
  share the URL but have different device tokens.

### Diagnostic flow

1. Click the button → check **single-HCA loopback** first
   (default mode). If that fails, the RDMA stack itself is
   broken — fix GID / port state / driver before anything else.
2. If single-HCA loopback works, flip the toggle → run **two
   HCAs same host**. If THAT fails, the issue is wire reachability
   (cable / shared switch / PFC / firmware loopback support) —
   not the RDMA stack.

Splits the failure surface cleanly into "stack" vs "wire."

### Files changed
- `widgets/rdma_topology_dialog.py` (picker refactor, toggle,
  client combo + status invariants, parent button label/tooltip).
- `pyproject.toml` (0.5.147 → 0.5.148).
- `tests/test_v05147_loopback_buttons.py` (21 tests — 17 from
  v0.5.147 updated to the tuple API + 4 new ones pinning the
  two-HCA mode's invariants).

### Verified
```
$ ./venv/bin/pytest tests/test_v05147_loopback_buttons.py -q
21 passed in 0.05s

$ ./venv/bin/pytest tests/ -q -k "rdma or perftest or topology"
264 passed, 2562 deselected
```

---

## [0.5.147] - 2026-06-14

**One-click Loopback buttons in Blast + Topology RDMA dialogs.**

Operator (after v0.5.146 exposed the real perftest error on a
same-host two-HCA `rocep43s0f0 ↔ rocep43s0f1` setup that wasn't
actually a working loopback):

> "add an explicit 'Loopback test'"

Same-host loopback IS supported by perftest — it's the canonical
RDMA smoke test (`ib_send_bw -d mlx5_0` on both sides). Until
v0.5.147 the operator had to type the same device into two text
areas (Topology) or pick the same combo entry twice (Blast). Now
it's one button.

### Blast a RDMA Flow dialog

* New **"↔ Use server device for loopback (same HCA on both sides)"**
  button below the device combos. Click → server's device combo +
  IB-port spin are mirrored onto the client side.
* Disabled when `server_tg_url != client_tg_url` (loopback only
  makes sense between processes on the same host). Tooltip
  explains why.
* Handles the probe race: if the client combo hasn't received
  devices yet, the mirror adds the entry with the right userData
  rather than dropping the selection.

### RDMA Topology Test dialog

* New **"↔ Loopback test (same HCA on both sides)"** button
  beneath the endpoints box.
* Click → opens `_LoopbackPickerDialog`: pick a TG, pick a HCA,
  click OK. Both Server-endpoints AND Client-endpoints text areas
  are set to the SAME `<tg_url> <device>` line.
* Replaces rather than appends — loopback is a focused smoke
  test; tacking it onto an existing multi-endpoint config would
  confuse the topology expander.
* Surfaces "(no HCAs)" + a pointer to Setup RDMA when
  `/api/rdma/devices` returns empty.
* OK button is disabled until the device probe completes — no
  accidental accept on `(probing…)`.

### Why this matters

The canonical troubleshooting path for RDMA failures:

1. Click Loopback → run on a single HCA. If it works, the RDMA
   stack on this server is healthy.
2. Switch to the two-HCA configuration. If THAT fails, the issue
   is link reachability / GID / PFC config, not the RDMA stack.

Splits the diagnostic surface so operators know whether to look
at the driver or at the wire.

### Files changed
- `widgets/rdma_blast_flow_dialog.py` (`_loopback_btn`,
  `_mirror_server_to_client`).
- `widgets/rdma_topology_dialog.py` (`_loopback_btn`,
  `_open_loopback_picker`, new `_LoopbackPickerDialog` class).
- `pyproject.toml` (0.5.146 → 0.5.147).
- `tests/test_v05147_loopback_buttons.py` (17 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05147_loopback_buttons.py -q
17 passed in 0.05s
```

---

## [0.5.146] - 2026-06-14

**perftest rc!=0 error filters out the config-dump banner so
operators see the actual diagnostic.**

Operator screenshot (RDMA Topology Test status bar):

```
1 pair | 0 running | 1 done | err: perftest exited rc=1:
CQ Moderation : 1 CQE Poll Batch : 16 Mtu : 1024[B]
Link type : Ethernet CPU freq : 2394[MHz] GID index …
```

None of that is an error. It's perftest's CONFIG DUMP banner —
the `Title : value` block it prints to stdout before the data
rows start. When perftest fails to even begin a transfer, those
banner lines are the last content in stdout, and the v0.4.0 error
builder (`tail = stdout_tail[-10:]`) surfaced them verbatim. An
ib_send_bw that died because of a PFC mismatch or wrong GID
index looked like a wall of meaningless config text.

### Fix

* New helper `_filter_perftest_noise(lines)` in
  `utils/rdma_perf.py`. Drops lines matching the structural
  banner pattern `<title-case tokens> : <value>` (Mtu, CPU freq,
  Link type, GID index, Connection type, CQ Moderation, …). Lines
  carrying actionable hints (`error` / `fail` / `couldn't` /
  `refused` / `denied` / `timed out` / `unable` / `no such` / …)
  are always preserved, even when they superficially look like
  headers — so `Status : Connection refused` survives.
* `_format_rc_error(rc, stdout_tail)` builds the `job.error`
  string. When the filtered tail is non-empty, joins the last 6
  lines, clipped to 400 chars. When EVERYTHING was banner noise,
  returns a clear:
  > perftest exited rc=N with no diagnostic on stdout/stderr —
  > check the full job log via /api/rdma/perftest/job/<id>.
  > Common causes: PFC/ECN mismatch, wrong GID index, RoCEv2
  > disabled on the NIC, or peer firewall blocking the perftest
  > control TCP port.
* The rc!=0 finalize block in `_reader_thread` now calls the
  helper instead of slicing raw stdout.

### Files changed
- `utils/rdma_perf.py` (~50 lines: regexes, two helpers, wiring).
- `pyproject.toml` (0.5.145 → 0.5.146).
- `tests/test_v05146_perftest_error_filter.py` (14 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05146_perftest_error_filter.py -q
14 passed in 0.05s

$ ./venv/bin/pytest tests/ -q -k "rdma or perftest"
239 passed, 2566 deselected
```

---

## [0.5.145] - 2026-06-14

**Hotfix: v0.5.144 iface-loss renderer crashed on cold start.**

Operator hit, immediately after upgrading to v0.5.144:

```
File "traffic_client/statistics_section.py", line 1874, in
update_statistics_table
    peer = filtered_statistics.get(peer_name) or
           merged_statistics.get(peer_name)
NameError: name 'filtered_statistics' is not defined
zsh: abort      venv/bin/python run_tgen_client.py
```

`filtered_statistics` and `merged_statistics` are local variables
in `_on_stats_fetch_finished` — the call site that builds the
dict and hands the filtered version to `update_statistics_table`.
Inside the renderer, the dict is named `statistics` (the
parameter). My v0.5.144 patch wrote the wrong names; static
analysis happily compiled it but the first call site exploded.

### Fix

`peer = statistics.get(peer_name)` — one-line scope correction.

### Files changed
- `traffic_client/statistics_section.py` (one line)
- `pyproject.toml` (0.5.144 → 0.5.145)
- `tests/test_v05145_iface_loss_scope_hotfix.py` (5 tests pinning
  the renderer source + an inline-stub behavioral test)

### Verified
```
$ ./venv/bin/pytest tests/test_v05145_iface_loss_scope_hotfix.py \
                    tests/test_v05144_iface_loss_phy_pair.py -q
20 passed in 0.19s
```

---

## [0.5.144] - 2026-06-14

**Iface Packets Lost / Loss % now use PHY pair counters, not the
undercounted per-stream rx_count.**

Operator (screenshot of Interface Statistics):

```
TG 0 - ens2f0np0    TG 0 - ens2f1np1
TX 23.66 Mfps       TX 0 fps
RX 0 fps            RX 20.55 Mfps
…
Packets Lost: 824,561,154    824,561,154    ← both halves
Loss %:        99.37%         99.37%        ← wildly wrong
```

The TX iface really sent 830M frames and the RX iface really
received ~820M on the wire — true loss ~1.2%, not 99%.

### Root cause

v0.5.139 fed the iface loss math from `stream.tx_count` and
`stream.rx_count`. But `stream.rx_count` is whatever the RX engine
(scapy sniffer / DPDK rx_worker / etc.) was able to count — and
under line-rate blast, that engine drops most frames before
binning them (the entire srv06 saga from v0.5.114 onward).

So the math became:
- `tx_for_loss` = 830M (real — DPDK tx_worker is accurate)
- `rx_for_loss` = 5.2M (per-stream rx_count — wildly undercounted)
- lost = 825M → 99.37% loss displayed

The PHY counters that v0.5.135 already wires into
`/api/interfaces` (`tx_packets_phy` / `rx_packets_phy` from
`ethtool -S` on Mellanox) see what actually crossed the wire. We
just weren't using them for the loss row.

### Fix

* New pure helper `compute_iface_pair_loss(own_phy_tx, own_phy_rx,
  peer_phy_tx, peer_phy_rx)` in `traffic_client/statistics_section.py`.
  Returns `(lost, loss_pct)` where `pair_tx = max(own.tx, peer.tx)`
  and `pair_rx = max(own.rx, peer.rx)`. Clamps negatives to zero.
* `merged_statistics[iface]` now seeds `phy_tx` / `phy_rx` from
  `interface.get("tx" / "rx", 0)` (wire-truth) and tracks a
  `peer_ifaces: set()` populated as streams are processed
  (`tx_iface ↔ rx_iface` learn about each other).
* The (10)/(11) renderer block calls the helper with own + peer
  PHY counters. BOTH halves of a back-to-back pair display the
  same loss number.
* PHY counters added to the cumulative-counter preservation tuple
  so a single-fetch glitch doesn't blank the loss row.
* When no peer + no traffic: cell shows "—" (not 0, not 100%).

### Files changed
- `traffic_client/statistics_section.py` (~70 lines: helper, seed,
  peer wiring, renderer rewrite, preservation).
- `pyproject.toml` (0.5.143 → 0.5.144).
- `tests/test_v05144_iface_loss_phy_pair.py` (15 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05144_iface_loss_phy_pair.py -q
15 passed in 0.25s
```

The screenshot-scenario test pins the operator's exact numbers:
830M PHY-tx, 820M PHY-rx → 10M lost, 1.20% loss, identical on
both halves.

---

## [0.5.143] - 2026-06-14

**RDMA Topology Test: pick endpoints from registered TGs + clarify
"device" naming.**

Operator (screenshot of the RDMA Topology Test dialog):

> "allow user to pick server and client endpoints from the existing
> server, also how does interface is selected from the menu?"

Two related gaps in `widgets/rdma_topology_dialog.py`:

1. The Server/Client endpoint text areas required hand-typing
   `http://srv01:5050 mlx5_0` per line. No way to enumerate the
   registered TG fleet. Unlike Blast a RDMA Flow (which gets the
   server URL handed in by the menu), Topology Test asks for a list,
   and the list was operator-typed.
2. The second token's role was ambiguous. Operators conflated it
   with the Ethernet iface picker used elsewhere in the GUI
   (`ens2f0np0`), when really it's the RDMA HCA name (`mlx5_0`)
   addressed directly via libibverbs.

Plus a latent bug: the pre-populate path called
`self._selected_servers()` (typo). The surrounding `try/except`
swallowed the AttributeError, so the starter line never showed up.

### What's new

* **New "Pick from servers…" button** next to each side header.
  Opens a multi-server picker (`_EndpointPickerDialog`) that:
    - Lists every registered TG (from `self.server_interfaces`).
    - Lazily fetches `/api/rdma/devices` per server (async — doesn't
      block the GUI thread).
    - Renders a tree: server → checkboxes per HCA, showing state +
      vendor/FW so the operator can see at a glance which HCA is
      Active / Down.
    - On accept, appends `<tg_url> <device>` lines to the parent
      text area (preserves any lines already there).
  Button is disabled with a tooltip when no TGs are registered yet.
* **Inline help line** under the endpoint editors:
  > **device** = RDMA HCA name (e.g. `mlx5_0`) — this is the
  > InfiniBand verbs device, NOT an Ethernet interface
  > (`ens2f0np0`). perftest addresses the HCA directly via
  > libibverbs.
* **Menu wiring**: `traffic_client/rdma_menu_actions.py` now passes
  the registered-TG set into the dialog via the new
  `known_servers=[(url, label), …]` kwarg.
* **Typo fix**: `self._selected_servers()` →
  `self._get_selected_servers()` so the starter-line
  pre-populate now actually runs when one or more servers are
  highlighted in the tree.

### Files changed
- `widgets/rdma_topology_dialog.py` (constructor kwarg, picker
  button, inline help, new `_EndpointPickerDialog` class).
- `traffic_client/rdma_menu_actions.py` (pass `known_servers`,
  fix typo).
- `pyproject.toml` (0.5.142 → 0.5.143).
- `tests/test_v05143_topology_endpoint_picker.py` (19 tests).

### Verified
```
$ ./venv/bin/pytest tests/test_v05143_topology_endpoint_picker.py -q
19 passed in 0.06s
```

---

## [0.5.142] - 2026-06-15

**Hotfix: cold-start client AttributeError on _stream_baselines.**

Operator hit on first launch after upgrading:

```
File "traffic_client/statistics_section.py", line 1989, in
update_stream_statistics_table
    _prev_tx = self._stream_baselines.setdefault(
AttributeError: 'TrafficGeneratorClient' object has no attribute
                '_stream_baselines'
zsh: abort      venv/bin/python run_tgen_client.py
```

v0.5.140's loss-latch code directly accessed
`self._stream_baselines.setdefault(...)`. That dict is normally
created by `main.py`'s Clear Stats handler — but on a fresh
client (no Clear Stats clicked yet) the attribute doesn't exist
→ AttributeError → GUI aborts on the first poll cycle.

### Fix

Lazy init of `_stream_baselines` in the latch block, mirroring
the `_latched_loss_pct` pattern already in v0.5.140:

```python
_baselines = getattr(self, "_stream_baselines", None)
if not isinstance(_baselines, dict):
    self._stream_baselines = {}
    _baselines = self._stream_baselines
```

### Files touched

- `traffic_client/statistics_section.py` — lazy-init guard
  before the latch's counter-reset bookkeeping.
- `tests/test_v05142_cold_start_no_baselines.py` — 6 cases:
  source no longer has the crashing direct-access pattern;
  lazy-init present; cold-start first poll doesn't raise; later
  polls reuse the same dict; latch on stop still works on
  cold-start; non-dict attr value is overwritten not crashed on.

## [0.5.141] - 2026-06-15

**Fix: Blast RDMA Flow + Topology Test jobs appear in the Streams tab.**

Operator report: "i don't see RDMA flows pls check the gap in RDMA"

### Root cause

Two RDMA paths in the server, only ONE registered with `stream_tracker`:

1. **Per-stream `engine=rdma`** (Add Stream dialog → engine combo
   "RDMA"): handled by `utils/rdma_stream_engine.start_rdma_stream`
   which DID call `stream_tracker.add_stream(...)`. Streams tab saw it.

2. **Standalone RDMA flows** (Tools → RDMA → Blast a RDMA Flow /
   Topology Test): dialogs POST directly to
   `/api/rdma/perftest/start`. That route only touched
   `utils.rdma_perf` job registry + `utils.rdma_handshake`. It
   NEVER touched `stream_tracker`. So flows from these dialogs
   appeared only in `/api/rdma/perftest/jobs` — invisible in the
   main Streams tab that operators were checking.

Confirmed on srv06: `curl /api/streams/stats?status=all` showed 4
streams, **zero tagged RDMA**, despite an operator having tried
to start one.

### Fix

Extracted the tracker-registration plumbing from
`start_rdma_stream` into a shared helper
`register_perftest_with_tracker(tracker, stream_id, job_id,
interface, stream_name, test, msg_size, stop_event, ...)`.

- Per-stream path still uses it (refactor; no behavior change).
- `/api/rdma/perftest/start` now calls it for **client-role** jobs.
  Server-role is skipped — server is a passive listener; mirroring
  it would duplicate every Blast pair row.

### Streams tab now shows for Blast / Topology flows

- **Stream Name**: `note` from the dialog (or synthetic
  `rdma-{test}-{job_id[:8]}` fallback)
- **Interface**: the RDMA device (e.g. `mlx5_0`) — groups under
  the HCA rather than mixing with Ethernet ifaces
- **Engine**: `RDMA {Send|Write|Read|SendL|WriteL|ReadL}` (purple,
  the existing v0.3.12 RDMA label)
- **TX Count**: synthesized from `final_iterations × msg_size_bytes`
  as the poll thread sees the perftest job progress
- **Status**: Running → Stopped tracks the perftest lifecycle

### Why client-only

Each Blast pair spawns TWO perftest jobs: a client and a server.
The client is the TX initiator (it sends BW or LAT iterations);
the server is a passive listener that mirrors. Registering both
would put two rows in the Streams tab for one logical flow.
Operators see the client's row; the handshake_id links them in
the RDMA Jobs dialog for paired views.

### Failure tolerance

If tracker registration raises (e.g., duplicate stream_id), the
helper logs + continues. The perftest job itself still runs;
only the Streams row is missing. Better to surface the flow's
perftest stats via the existing RDMA Jobs dialog than to kill
the whole start because of a row-rendering hiccup.

### Files touched

- `utils/rdma_stream_engine.py` — new `register_perftest_with_tracker`
  helper; `start_rdma_stream` refactored to delegate.
- `run_tgen_server.py` — `/api/rdma/perftest/start` calls the
  helper for client-role jobs.
- `tests/test_v05141_rdma_blast_in_stream_stats.py` — 9 cases:
  helper exported; per-stream path delegates (no double-add);
  perftest/start route calls helper inside `if role == "client":`
  guard; helper shape (stream_id / interface / frame_size / engine
  / rdma sub-dict); runtime_engine marked rdma; synthetic name
  fallback; tracker failure swallowed; missing
  mark_runtime_engine tolerated.

## [0.5.140] - 2026-06-15

**Feature: Loss % latches the final value on stream stop (Spirent-like).**

Operator referenced Spirent's behavior. In Spirent panels, when
a stream stops the Loss % column **freezes** on the last
observed value rather than blanking. Operator wants the same —
read the final test result without racing to catch the cell
before it wipes.

### Behavior

Stream-stats Loss % now follows this lifecycle:

| State                              | Cell shows                  |
|------------------------------------|-----------------------------|
| Never ran (warmup, tx_count == 0)  | `—`                         |
| Running with rates                 | rate-based loss (live)      |
| Stopped (rates went to 0)          | **last rate-based value**   |
| Stream restarted (counter reset)   | recompute from new session  |
| Clear Stats clicked                | `—` again                   |

The cache `self._latched_loss_pct[stream_id]` holds each stream's
most recent rate-based loss. It's:

- Updated on every running sample.
- Surfaced when rates aren't observable (stream stopped).
- Dropped when `tx_count` drops below the previous reading
  (counter reset = stream restart = fresh session).
- Purged alongside `_stream_baselines` when the operator clicks
  Clear Stats.

### Why this fits the saga

v0.5.137 made loss rate-based instead of cumulative (no more
99.39% from out-of-sync rx_worker counters). v0.5.138 hid it
entirely on stop (cumulative was misleading). v0.5.139 moved
the cumulative loss to the iface stats panel (where it persists
naturally). v0.5.140 brings stream-stats in line with Spirent
behavior — last value latched on stop.

### Files touched

- `traffic_client/statistics_section.py`:
  - Loss-pct block in `update_stream_statistics_table()` gains
    the latch lookup/update + counter-reset detection.
  - Clear Stats purge loop includes `_latched_loss_pct`.
- `tests/test_v05140_spirent_loss_latch.py` — 9 cases:
  running computes + caches; stop latches; never-ran → None;
  restart drops latch + recomputes; per-stream isolation; jitter
  tracked; srv06 UEC scenario; zero loss latches zero; Clear
  Stats purge.

## [0.5.139] - 2026-06-14

**Feature: Interface stats panel gains Packets Lost + Loss % rows.**

Companion to v0.5.138. The stream-stats Loss % went None-on-stopped
in v0.5.138 (correctly — cumulative loss there is misleading
because rx_worker counter resets). But the operator still wants
to see the **session loss** somewhere, and have it **persist
after stop**.

The Interface stats panel is the right place: it already shows
cumulative byte/packet counts that persist across stop/restart.
Two new rows fit naturally there.

### New rows

**Packets Lost** — `tx_for_loss − rx_for_loss`, summed across
all streams whose TX or RX iface is this one. Cumulative count
(persists). Red foreground when > 0.

**Loss %** — `packets_lost / tx_for_loss × 100`. Color scale
matches the stream-stats Loss % cell:
- > 50% — red, bold
- > 10% — amber
- > 0% — muted dark
- = 0% — neutral
- iface with no TX activity (pure RX peer or idle iface): "—"

Both numbers respect Clear Stats — baseline-subtracted via the
same `adjusted()` helper as the other cumulative rows.

### Aggregation logic

For each running stream, the merger contributes its `tx_count`
to BOTH ifaces' `tx_for_loss` (TX iface and RX iface), and its
`rx_count` (when flow_tracking is on) to BOTH ifaces' `rx_for_loss`.

That way, in a back-to-back pair (ens2f0np0 ↔ ens2f1np1) the
SAME "lost N packets" appears under both columns — operators
can read it from either side and get the same answer.

### Behavior on srv06 after stream stop

Operator's previous screenshot (3 stopped streams) will now show:
- ens2f0np0: Packets Lost = (sum across all stopped streams)
- ens2f0np0: Loss % = (cumulative %)
- ens2f1np1: same numbers (matching back-to-back pair)

Numbers stay until operator clicks Clear Stats.

### Files touched

- `traffic_client/statistics_section.py`:
  - `merged_statistics[iface]` now initializes `tx_for_loss = 0`
    and `rx_for_loss = 0`.
  - TX-iface aggregation block adds the stream's tx_count and
    rx_count (when flow_tracking on) to those totals.
  - RX-iface aggregation block does the same.
  - `statistics_table.setRowCount(12)` (was 10).
  - Vertical header labels gain "Packets Lost" and "Loss %"
    (both init at line ~449 and rebuild at line ~1621).
  - Render loop adds row 10 (Packets Lost) and row 11 (Loss %)
    cells with baseline-subtraction + color scale.
- `tests/test_v05139_iface_loss_stats.py` — 11 cases: row
  labels in both header lists, row count bumped, merged stats
  initializes the loss counters, both TX and RX aggregation
  blocks contribute, render math (zero traffic → "—", no loss,
  partial, full, clamp-on-RX>TX, persists after stop).

## [0.5.138] - 2026-06-14

**Fix: Loss % shows "—" for stopped streams (cumulative fallback removed).**

Operator screenshot showed three stopped streams with bogus loss %:

| Stream | TX Count    | RX Count | Loss %   | Status  |
|--------|-------------|----------|----------|---------|
| ICMP   | 2,600       | 368      | 85.85%   | Stopped |
| UEC    | 330,752     | 4        | 100.00%  | Stopped |
| UDP    | 136,678,720 | 8,906    | 99.99%   | Stopped |

All three came from v0.5.137's cumulative fallback: `(TX − RX) / TX × 100`.
Arithmetically correct, but meaningless because the rx_worker
counter at shutdown isn't aligned with the tx_worker counter.

### Why cumulative loss is almost always wrong

- rx_worker stops before tx_worker finishes flushing → false loss
- rx_worker counter is zeroed on re-spawn (post-config-change,
  lcore reallocation post-v0.5.131/132/134) → false loss
- cumulative reflects OLD config if operator changed it mid-test
  → wrong

### Fix

v0.5.138 drops the cumulative fallback entirely. Loss % is now
computed only when:

- `tx_count > 0` (stream has emitted at least one packet)
- `tx_rate > 0` (stream is currently emitting)
- `rx_rate is not None` (RX counter is observable)

All else → `None` → renderer shows muted `—`. The TX Count and
RX Count columns are still displayed for operators who want to
do the math themselves and know the counters are aligned.

### Behavior matrix

| Stream state                           | Pre-fix | Post-fix |
|----------------------------------------|---------|----------|
| Running, rate-based loss observable    | rate %  | rate %   |
| Running, no RX rate yet (warmup)       | cumul   | —        |
| Stopped, both rates 0                  | cumul   | —        |
| Just stopped, rates still in last poll | rate %  | rate %   |
| TX never fired (tx_count = 0)          | —       | —        |

### Files touched

- `traffic_client/statistics_section.py` — loss_pct branch
  simplified; cumulative fallback removed.
- `tests/test_v05137_rate_based_loss.py` — updated 3 tests that
  asserted cumulative fallback behavior to assert None instead.
- `tests/test_v05138_no_loss_when_stopped.py` — 11 new cases:
  the exact ICMP / UEC / UDP screenshot scenarios; running
  cases still work; transition states; defensive (tx_count=0,
  negative, flow_tracking off).
- `tests/test_v0_3_7_polish.py` — updated the formula-pin test
  to match v0.5.137/138's rate-based formula.

## [0.5.137] - 2026-06-14

**Fix: stream-stats Loss % uses rates when running.**

Operator screenshot: UEC stream showed Loss = 99.39% despite
TX rate 23.71 Mpps and RX rate 20.57 Mpps. Real instantaneous
loss `= (23.71 − 20.57) / 23.71 = 13.2%`.

The 99.39% came from cumulative counts:
- TX 3,218,807,168 cumulative
- RX 19,481,462 cumulative
- `(TX − RX) / TX = 99.39%` ✓ arithmetic, but meaningless

### Root cause

TX had been counting for `3.2B / 23.71M = ~135 seconds`. RX had
been counting for `19.5M / 20.57M = ~0.95 seconds`. The
rx_worker was respawned (lcore reallocation post-v0.5.131/132/134)
and started its counter fresh, while tx_worker kept its history.
Cumulative loss is meaningless when the two counters started at
different times.

### Fix

Stream-stats Loss % column now prefers rate-based loss when the
stream is running:

```python
if tx_rate > 0 and rx_rate is not None:
    loss = max(0.0, (tx_rate - rx_rate) / tx_rate * 100)
elif isinstance(rx_count, int):
    # stopped / zero-rate — cumulative still useful as session summary
    loss = (tx_count - rx_count) / tx_count * 100
```

Rate-based answers the operator's real question: "of what I'm
sending right now, what fraction is being dropped?" — independent
of counter start times.

### Edge cases handled

- **rx_rate > tx_rate** (sample-window phase offset between
  rx/tx_worker poll cycles): clamped to 0 so the cell doesn't
  flash a confusing negative loss.
- **TX rate = 0** (stopped stream): falls back to cumulative —
  that's the session summary the operator wants on stopped rows.
- **TX count = 0** (warmup): None → muted "—" placeholder.
- **flow_tracking off** (no rx_count): None.

### Files touched

- `traffic_client/statistics_section.py` — loss_pct calculation
  in `update_stream_statistics_table()` now uses rates first.
- `tests/test_v05137_rate_based_loss.py` — 11 cases: the exact
  srv06 UEC scenario; equal rates → 0; rx_rate=0 → 100%;
  negative clamped; stopped fallback; tx_rate=0 fallback;
  tx_count=0 → None; rx_rate=None fallback; small 2% loss;
  50% loss; flow_tracking off → None.

## [0.5.136] - 2026-06-14

**Fix: stream-stats table TX/RX Bit Rate cells use real frame_size.**

Operator screenshot caught it: UEC stream firing at 23.77 Mpps
with frame_size=1000 showed "TX Bit Rate = 12.17 Gbps" in the
Stream Statistics table. Backsolving: `12.17e9 / (23.77e6 × 8)
= 64 bytes/pkt` — the renderer's fallback default.

### Root cause

`update_stream_statistics_table()` in `statistics_section.py`
builds `all_streams` from `/api/streams/stats`. The per-row
dict copied `tx_rate`, `rx_rate`, `tx_count`, `rx_count`,
`status`, `engine` — but **not `frame_size`**. The render loop
at line ~2106 did `stream.get("frame_size") or 64` and always
fell back to 64.

### Fix

The per-row dict now includes `frame_size`:

```python
all_streams.append({
    ...
    "frame_size": stream.get("frame_size", 64),
})
```

The API entry already carries it (we use it in the
`merged_statistics` aggregation above — line 1148, 1180); just
forward it into the row dict the renderer reads.

### Operator-visible result (srv06 UEC, 23.77 Mpps, 1000B):

Pre-fix:  TX Bit Rate = 12.17 Gbps  (frame_size dropped, used 64)
Post-fix: TX Bit Rate = ~190 Gbps   (frame_size = 1000 honored)

That brings the stream-stats column in line with the Interface
stats panel (which already shows ~190 Gbps from the wire
counter side).

### Falls back cleanly

- Missing `frame_size` in API entry → still falls back to 64
  (no regression vs pre-v0.5.136).
- String `frame_size` ("1000" from dialog persistence) →
  coerced to int safely.
- Garbage value → falls back to 64.

### Files touched

- `traffic_client/statistics_section.py` — add `frame_size` to
  the per-row dict in `update_stream_statistics_table()`.
- `tests/test_v05136_stream_frame_size_propagation.py` — 7
  cases: the exact srv06 UEC scenario (23.77 Mpps × 1000B →
  ~190 Gbps); 512B / 1500B frame sizes; RX uses frame_size
  too; missing / string / garbage / zero defensive cases.

## [0.5.135] - 2026-06-14

**Fix: /api/interfaces uses Mellanox PHY counters when available.**

Operator audit on srv06 found Interface stats bps disagreed with
Stream stats bps. Root cause: `/api/interfaces` reads
`psutil.net_io_counters()` which goes through `/proc/net/dev`
(the kernel netdev path). On Mellanox kernel-bound NICs, DPDK
PMD bypasses the kernel TX queue → kernel TX byte counter is
blind to DPDK traffic.

srv06 ground truth (over iface lifetime):
- kernel `tx_packets` = 3.2 M
- PHY `tx_packets_phy` = **140 BILLION** (DPDK + scapy combined)
- kernel `tx_bytes` = 404 MB
- PHY `tx_bytes_phy` = **109 TB**

That's why the Interface stats panel showed TX bps near zero
while Stream stats showed 100+ Gbps — same wire, two views,
one measurement source was blind.

### Fix

`/api/interfaces` now also reads Mellanox PHY counters via
`ethtool -S <iface>` and prefers them when present:
- `rx_packets_phy`, `tx_packets_phy`
- `rx_bytes_phy`, `tx_bytes_phy`

These are HARDWARE-level counters that see ALL traffic regardless
of who's driving the queues. Use the higher of the two values per
metric (PHY ≥ kernel always; kernel never sees more than HW saw).

### Falls back cleanly

- Non-Mellanox NICs (Intel, Broadcom): `_phy` fields don't appear
  in `ethtool -S` output → helper returns None → caller falls
  back to kernel netdev counts (pre-v0.5.135 behavior).
- No `ethtool` binary (rare): same fallback.
- `ethtool -S` timeout / error: same fallback.
- Cached briefly (500 ms TTL) so GUI polling doesn't fork an
  ethtool per iface per request.

### Files touched

- `run_tgen_server.py` — new `_mellanox_phy_counters(iface)`
  helper with 500 ms cache. `/api/interfaces` calls it and
  promotes the four PHY values when they exceed the kernel
  numbers.
- `tests/test_v05135_mellanox_phy_counters.py` — 8 cases:
  parser extracts only `_phy` keys; returns None when ethtool
  missing / nonzero exit / no PHY keys; ignores malformed lines;
  caches briefly (suppresses repeat calls); per-iface cache
  separation; timeout handling.

### Other bit-rate audit findings (NOT bugs, just gotchas)

1. **Neither bps formula includes wire overhead** (preamble 7 +
   SFD 1 + IFG 12 = 20 bytes/pkt). Both show L2 bps. "200 Gbps"
   target shows as ~196 Gbps on a 1000B-frame stream.
2. **FCS asymmetry**: kernel netdev TX usually includes FCS,
   RX usually doesn't. Stream stats use raw `frame_size`
   (tx_worker `--size`, no FCS). 4 bytes/pkt difference.
3. **Multi-stream aggregation**: iface counts ALL wire bytes;
   stream stats sums only known streams. Background traffic
   (LLDP, ARP, multicast) shows in iface but not stream.
4. **Frame padding to 60B**: tx_worker pads frames below the
   Ethernet 60-byte minimum. Configured `frame_size=40` → wire
   frame is 60B + FCS.

## [0.5.134] - 2026-06-14

**Fix: rx_worker lcore picker excludes lcores held by other workers.**

After v0.5.131-133, srv06 multi-stream still pinned two rx_workers
to the same lcore set `0,1,...,16`. Two DPDK processes sharing
physical CPUs means the Linux scheduler ping-pongs them — every
context switch reaches into the wrong PMD hot path.

Operator state at v0.5.131:
- UEC: tx=21.87 Mpps, rx=13.98 Mpps, hw_imissed=5.8B
- UDP: similar overlap

### Fix

`_pick_rx_lcores` gains a `reserved: set[int]` param. The lcores
in that set are skipped over when picking from the NUMA cpulist.

New helper `_collect_used_lcores()` parses each registered
rx_worker's cmd list for its `-l` arg and unions the lcore sets.
The auto-scaler passes the result as `reserved` so each new
rx_worker gets exclusive ownership of its CPUs.

### Behavior on srv06

| Scenario           | v0.5.133                     | v0.5.134                                |
|--------------------|------------------------------|------------------------------------------|
| 1st stream         | lcores 0..16 (16q)           | lcores 0..16 (unchanged)                 |
| 2nd stream         | lcores 0..8 (OVERLAP!)       | lcores 9..15,32,33 (disjoint, NUMA-local)|
| 3rd stream         | lcores 0..5 (OVERLAP × 3)    | lcores 32..37 (disjoint, SMT siblings)   |

### Fallback contracts preserved

- No reserved set / empty → identical to v0.5.132/133 behavior.
- NUMA unavailable → fallback to `range(needed)`, reserved
  ignored (no notion of which lcore is which without sysfs).
- After exclusion not enough cores remain → fallback to
  `range(needed)`, accepting overlap rather than under-provisioning.

### Files touched

- `run_tgen_server.py` — `_pick_rx_lcores` gains `reserved` param;
  new `_collect_used_lcores()` helper; line-rate auto-scaler
  passes the reserved set on every new stream spawn.
- `tests/test_v05134_lcore_no_overlap.py` — 13 cases: picker
  with reserved (happy path, NUMA chunks, fallback when too few
  free, no-reserved unchanged, fallback path ignores reserved);
  collector (empty / single / union / lookup failure / malformed
  cmd); e2e first stream unchanged + second stream disjoint +
  third stream disjoint across both prior reservations.

## [0.5.133] - 2026-06-14

**Fix: rx_queue cap divides by concurrent DPDK stream count.**

After v0.5.131 bumped the line-rate cap to 16 queues, srv06 saw
EAL `Cause: mbuf_pool` when starting a 2nd concurrent DPDK
stream. Each rx_worker and tx_worker reserves its own mempool
(~256 MB per worker at 16 queues). 2 streams × (rx + tx) ≈ 1 GB
hugepages — over budget on hosts with the default allocation.

```
tx_worker[2413045]: EAL: Error - exiting with code: 1
                    Cause:
                    mbuf_pool
ERROR:dpdk_tx_worker:[dpdk] stream 'UDP' failed with exit code 1
```

### Fix

`_line_rate_queue_cap` now accepts `active_dpdk_streams`. When N
streams are already running, the new stream's cap becomes
`base_cap // (N + 1)`. Floor at 2 so RSS still helps even with
many concurrent streams.

New helper `_count_active_dpdk_rx()` reads
`utils.dpdk_rx_manager.registry()._handles` and returns the
number of rx_workers currently registered. The auto-scaler calls
it BEFORE picking the cap so the new stream knows the budget.

### Behavior matrix

| Scenario                          | base=14 (srv06) | base=8 (lean) |
|-----------------------------------|-----------------|---------------|
| First DPDK stream                 | 14 queues       | 8 queues      |
| Second concurrent stream          | 7 queues        | 4 queues      |
| Fourth concurrent stream          | 3 queues        | 2 queues      |
| Many streams (10+)                | 2 (floor)       | 2 (floor)     |

### Explicit operator override unaffected

`stream_data["rx_queues"] = N` bypasses the backoff entirely —
operators with explicit values know their hugepage budget.

### Mid-rate buckets unaffected

The 6/18 Mpps buckets still pick their original 2/4 queues. The
backoff only applies to the line-rate bucket because that's the
one v0.5.131 inflated.

### Files touched

- `run_tgen_server.py` — `_line_rate_queue_cap` gains
  `active_dpdk_streams` param; new `_count_active_dpdk_rx`
  helper. Line-rate auto-scaler counts active streams before
  picking the cap.
- `tests/test_v05133_concurrent_stream_mempool_backoff.py` —
  16 cases: cap divides by stream count, floor at 2, fallback
  path also backs off; counter handles lookup failure / empty
  registry / populated; e2e first/second/third stream + lcore
  string matches new queue count + explicit override bypasses
  + mid-rate buckets untouched.

## [0.5.132] - 2026-06-14

**Fix: rx_worker lcores picked from NIC's NUMA cpulist, not range(N).**

srv06 v0.5.131 follow-up. With the bucket cap lifted to 16, the
auto-scaler picked lcores `0,1,2,...,16` for the rx_worker. On a
dual-socket box that silently lands lcore 16 on **the wrong NUMA
node** (srv06's NUMA node 0 is CPUs `0-15,32-47`; lcore 16 is on
node 1 — one full QPI/UPI hop from the NIC's memory).

Operator saw per-queue throughput halve as cap went from 8 → 16:
single-queue rate dropped 2.2 → 1.1 Mpps. Total stayed around
17 Mpps so the bump appeared neutral — but the wasted budget
was 50% per lcore.

### Fix

New helper `_numa_cpulist_for_pci(bdf)` returns the actual CPU
ID list from `/sys/devices/system/node/node<N>/cpulist`. Replaces
the count-only `_numa_cores_for_pci` (which is now a thin
wrapper).

New helper `_pick_rx_lcores(pci_bdf, queue_count)` picks
`queue_count + 1` lcores from that cpulist. Falls back to
`range(N)` when sysfs is unavailable (Mac / CI / single-socket).

On srv06: instead of `0..16` (one wrong-NUMA core) the auto-
scaler now picks `0..15, 32` — all 17 lcores on NUMA node 0.

### Operator-visible impact (srv06)

Pre-fix at line rate (v0.5.131): rx_rate ≈ 17.5 Mpps, per-queue
≈ 1.1 Mpps with one lcore on wrong NUMA.

Expected post-fix: per-queue rate restored to ~2.2 Mpps × 16
queues → headroom for ~35 Mpps line-rate absorption.

### Falls back cleanly

- Mac / CI runners: no `/sys/bus/pci` → `_numa_cpulist_for_pci`
  returns None → `_pick_rx_lcores` falls back to `range(N)` → no
  behavior change.
- Single-socket boxes (`numa_node = -1`): same fallback.
- NUMA node with fewer cores than the queue count needs: also
  falls back to `range(N)` rather than under-provisioning lcores.

### Files touched

- `run_tgen_server.py` — `_numa_cpulist_for_pci` (new),
  `_numa_cores_for_pci` (refactored to wrapper), `_pick_rx_lcores`
  (new). Line-rate auto-scaler branch now calls `_pick_rx_lcores`
  instead of building `range(cap + 1)` inline.
- `tests/test_v05132_numa_lcore_selection.py` — 15 cases: sysfs
  parser (dense, SMT-interleaved, single-cpu, single-socket,
  missing); picker (NUMA-aware happy path, fallback when NUMA
  absent, fallback when NUMA too small, NIC on node 1 symmetry);
  end-to-end through auto-scaler verifying NO lcore lands on the
  wrong NUMA node.

## [0.5.131] - 2026-06-14

**Fix: NUMA-aware rx_queue cap lifts line-rate auto-scaler from 8 to up to 16.**

After v0.5.128 (count flags) + v0.5.130 (dialog norm) shipped,
the srv06 UDP stream still showed a ~28% TX/RX gap at line rate:
- `tx_rate`: 24 Mpps, `rx_rate`: 17 Mpps  
- 5-tuple cycling at `dst_port_count=128` was correctly spreading
  RSS across all 8 rx_queues. Per-queue rate ~2.2 Mpps × 8 = 17.5
  Mpps total — exactly what the operator measured.

The bottleneck had moved from "single queue" to "per-lcore RX
throughput × queue count". srv06 has 64 cores / 2 NUMA nodes /
16 cores per socket, but the rx_worker was using only 8 queues
+ 9 lcores.

### Fix

`_maybe_start_dpdk_rx_for_stream` line-rate bucket cap is now
NUMA-aware. Two new module-level helpers:

- `_numa_cores_for_pci(bdf)` — reads
  `/sys/bus/pci/devices/<bdf>/numa_node` and
  `/sys/devices/system/node/node<N>/cpulist` to count CPUs on
  the NIC's NUMA node. Returns None on any failure.
- `_line_rate_queue_cap(pci_bdf)` — `max(8, min(16, numa_cores - 2))`.
  Reserves 2 cores for system + rx_worker main loop. Hard-cap
  at 16 to match rx_worker.c's `MAX_RX_QUEUES`.

Lean hosts / sysfs failures fall back to 8 — no regression for
small boxes or any host where the NIC's NUMA node can't be
determined.

### Operator-visible impact (srv06)

Before: line-rate cap = 8 queues, 9 lcores → 17.5 Mpps RX
After:  line-rate cap = 14 queues, 15 lcores → ~30+ Mpps RX
expected (per-queue rate × 14 instead of × 8)

### Files touched

- `run_tgen_server.py` — `_numa_cores_for_pci` + `_line_rate_queue_cap`
  helpers; line-rate bucket in `_auto_rx_queues_for_pps` now uses
  the cap
- `tests/test_v05131_numa_aware_queue_cap.py` — 15 cases: sysfs
  parser (dense, SMT-paired, single-CPU, single-socket, garbage,
  missing); cap function (fallback, lean host, srv06 16-core,
  hard 16 ceiling, SMT 32); end-to-end through autoscaler at line
  rate + lower buckets unchanged

### Lower buckets unchanged

The 6 / 18 Mpps buckets still pick 2 / 4 queues. Operators with
explicit `target_pps` below the line-rate threshold won't see
surprise lcore inflation.

## [0.5.130] - 2026-06-14

**Fix: stream dialog normalizes increment-flag ↔ count consistency.**

Follow-up audit after the v0.5.128 dst_port_count operator
report. Audited every increment/count field in the Add/Edit
Stream dialog (11 fields across MAC, VLAN, IPv4, IPv6, TCP, UDP)
and confirmed all 11 round-trip correctly through save/load.

### Root cause (real but separate from v0.5.128)

scapy and DPDK engines disagreed on what "cycle" means:
- `utils/dpdk_tx_worker._resolve_count_flags` — gates cycling on
  the **count value** (`>= 2`); ignores the increment checkbox
- `utils/generic.py` (scapy) — gates cycling on the **checkbox**
  `udp_increment_destination_port`; count is read only if checkbox=True

So a saved stream with `(count=64, checkbox=False)` cycled under
DPDK but stayed single-flow under scapy. Inverse: `(count=1,
checkbox=True)` "cycled" scapy by 1 port (no-op) but didn't cycle
DPDK. The persisted data could be in an internally inconsistent
state depending on dialog interaction order.

### Fix

`get_stream_details()` now invokes `_normalize_increment_flags()`
before returning the dict. Logic:
- UDP / TCP / VLAN (binary checkbox): force `checkbox = (count >= 2)`.
- IPv4 / IPv6 / MAC (Fixed/Increment/Decrement combo): only
  promote `Fixed → Increment` when count >= 2 (silent
  contradiction). User-chosen `Decrement` is respected. count=1
  with mode=Increment is left alone (a no-op cycle is a valid
  configuration, not a bug).

Count value is now authoritative for both engines.

### Files touched

- `widgets/stream_dialog.py` — `_normalize_increment_flags()` +
  `_coerce_count()` module-level helpers; called at the tail of
  `get_stream_details()`
- `tests/test_v05130_increment_normalization.py` — 18 cases:
  UDP/TCP/VLAN promote+demote, IPv4/v6/MAC combo promotion,
  Decrement preservation, garbage coercion, missing-section
  defensive paths, full Qt round-trip

### Operator-visible impact

If you saw the v0.5.128 srv06 symptom (count=64 in dialog, live
config shows count=1) it was **not** a save bug — round-trip
works. The likely path was: Edit dialog closed via Cancel/X
instead of OK, or stream needed stop → edit → start. After
v0.5.130, any save where the user typed a count without
matching the checkbox will still produce consistent data.

## [0.5.129] - 2026-06-14

**Fix: rx_worker port-filter treats `0` as "no filter", not literal port 0.**

After v0.5.128 went out, srv06 UEC stream still showed rx_count=0
despite tx_worker firing at 17 Mpps. Live debug via `ps aux | grep
rx_worker` revealed the UEC rx_worker was launched with
`--dst-port 0 --src-port 0`. The rx_worker honors those literally
— it requires every frame to have port==0 → silently rejects every
packet on the wire.

### Root cause

`_maybe_start_dpdk_rx_for_stream` in `run_tgen_server.py` reads
the UDP port fields from `protocol_data.udp` and forwards them as
`dst_port` / `src_port` kwargs to `rx_registry.start()`. The
stream dialog persists UDP fields as `"0"` for streams whose L4
isn't actually UDP — UEC emulation and ICMP fill in their own
protocol fields, not the udp.* fields. Pre-fix the auto-lifecycle
passed those zeros to rx_worker which then required every frame
to have port=0. RX counters stuck at 0 for the entire test.

### Fix

Added `_port_filter_or_none(v)` helper that coerces `0` (and
None / non-numeric / out-of-range) to None. None at the rx_worker
launcher means "skip the port-filter clause" — match any port.
Real ports (1-65535) pass through unchanged so UDP streams keep
their tight BPF.

### Files touched

- `run_tgen_server.py` — `_port_filter_or_none` helper + wire it
  into `_maybe_start_dpdk_rx_for_stream`'s dst_port/src_port
  kwargs
- `tests/test_v05129_zero_port_filter.py` — 8 cases: zero → None,
  real port passes through, missing udp section safe, garbage
  coerced, edge cases (port 1, 65535)

### Operational note

This bug predates v0.5.128 — UEC/ICMP streams in DPDK rx_engine
mode have been silently rejected since the auto-lifecycle landed.
The TX side was always firing correctly; only the matched_pkts
counter was broken.

## [0.5.128] - 2026-06-14

**Fix: DPDK tx_worker count-field flags read from protocol_data.**

v0.5.126/127 auto-scaled rx_worker to 8 queues for line-rate
streams, but the matching TX-side knobs to actually cycle the
5-tuple (so RSS distributes across queues) never made it to
tx_worker.

### Root cause

`run_stream()`'s count-flag block read only top-level short
names: `stream_data["dst_port_count"]`. The dialog stores its
value under `protocol_data.udp.udp_destination_port_count`
(nested + longer). So every dialog-driven stream's UI setting
of `dst_port_count=64` was silently discarded → tx_worker fired
with a single 5-tuple → RSS hashed everything to queue 0 →
multi-queue rx_worker provided zero benefit (saw 5.2 Mpps with
8 queues vs 6.4 Mpps with 1 queue, because the 7 idle lcores
were eating PCIe bandwidth).

### Fix

Extracted `_resolve_count_flags(stream_data)` as a module-level
helper. Reads both top-level (API-direct convention) and
`protocol_data.{ipv4,udp}` (dialog convention). Top-level wins
when set; otherwise nested value applies. Same shape of fix as
the VLAN-mode resolver helper (v0.5.124).

| field | top-level | protocol_data path |
|-------|-----------|---------------------|
| `--src-ip-count` | `src_ip_count` | `protocol_data.ipv4.ipv4_source_increment_count` |
| `--dst-ip-count` | `dst_ip_count` | `protocol_data.ipv4.ipv4_destination_increment_count` |
| `--src-port-count` | `src_port_count` | `protocol_data.udp.udp_source_port_count` |
| `--dst-port-count` | `dst_port_count` | `protocol_data.udp.udp_destination_port_count` |

### Tests

`tests/test_v05128_count_field_resolution.py` — 10 cases:

* Each of the 4 fields reads from protocol_data (the bug fix)
* Top-level short form still works (back-compat)
* Top-level takes precedence over protocol_data
* Count of 0 or 1 yields no flag
* Garbage value silently skips (no crash)
* All four at once
* No protocol_data → no flags (defensive)

Full suite: 2595 passed (+ 1 pre-existing order-dependent flake
in test_rx_worker_e2e, passes in isolation, not from this change).

## [0.5.127] - 2026-06-14

**Hot-fix: rx_worker auto-scale picks max queues for Line Rate
streams (target_pps == 0).**

v0.5.126 keyed the auto-scale off `_resolve_target_pps()`, but
streams configured as **Line Rate** (or with no explicit
`stream_pps_rate`) resolve to 0. The pre-fix bucket logic then
treated 0 the same as 100k and picked `rx_queues=1` — the
single-queue default that the whole v0.5.126 change was trying
to avoid. Result on srv06: tx_worker fired at ~24 Mpps with
`--pps 0`, rx_worker stayed single-queue, `hw_imissed` climbed
to 1.1 BILLION inside two minutes.

### Fix

Treat `pps == 0` the same as the top bucket (≥ 30 Mpps) — assume
Line Rate, max out queues and lcores. Operator can override
down via `stream_data["rx_queues"]` if they want fewer.

### Tests

`tests/test_v05126_rx_queue_autoscale.py` extended:

* pps=0 (explicit) → 8 queues
* No stream_pps_rate field → 8 queues (was: 1 queue)
* Other 7 cases preserved (low pps → 1, 6M → 2, 18M → 4, 30M+ → 8, overrides, clamps).

Full suite: 2586 passed, 1 skipped.

### Lesson

When auto-scaling on a runtime value, `0` almost always means
"unset / take the safe default." For a rate threshold, the safe
default is the TOP bucket, not the bottom — undersized is
silent (hw_imissed drops invisibly), oversized is just a few
unused lcores.

## [0.5.126] - 2026-06-14

**Fix: rx_worker auto-scales queues + lcores based on target pps.**

Caught on srv06 at 46 Mpps (line-rate-ish on 200G with 512B
frames): single-queue rx_worker hit
`hw_imissed: 3,397,304,661` — 3.4 BILLION packets dropped at the
chip's RX ring before software could consume.

### Root cause

`_maybe_start_dpdk_rx_for_stream` hardcoded `rx_queues=1`.
One DPDK lcore caps at ~6-7 Mpps for 512B frames on ConnectX-6;
beyond that the NIC ring overflows. Operator sees a huge TX/RX
divergence in the Interface Stats table without any signal in
the netgen UI pointing at the queue count.

### Fix

Auto-scale based on the stream's `target_pps`:

| target pps | rx_queues | lcores |
|-----------|-----------|--------|
| < 6 Mpps | 1 | 0,1 |
| 6..<18 Mpps | 2 | 0,1,2 |
| 18..<30 Mpps | 4 | 0,1,2,3,4 |
| >= 30 Mpps | 8 | 0,1,2,3,4,5,6,7,8 |

Operator can override explicitly via `stream_data["rx_queues"]`
and `stream_data["rx_lcores"]` for advanced tuning. Explicit
values clamp to 1..16 (the rx_worker.c MAX_RX_QUEUES limit);
invalid values fall back to 1 instead of crashing.

When auto-scale fires, the log line names the threshold so the
operator can correlate: `"[DPDK-RX] auto-scaling rx_queues=4
lcores=0,1,2,3,4 for target 20,000,000 pps"`.

### Tests

`tests/test_v05126_rx_queue_autoscale.py` — 8 cases:

* Each pps bucket (100k, 6M, 18M, 46M) picks the right tier
* Operator override (rx_queues, rx_lcores) wins over auto
* Garbage input clamps safely (16 → 16, 0 → 1, "bogus" → 1)
* Missing stream_pps_rate falls back to single-queue default

Full suite: 2585 passed, 1 skipped.

### Notes

* The thresholds are conservative (6 Mpps per queue). They
  match what the v0.5.125 srv06 test measured. On other chip
  generations or smaller frame sizes the per-queue ceiling
  differs — operators can override with explicit values.
* RSS hashing across N queues requires the rx_worker.c side to
  enable RSS in its `rte_eth_dev_configure` (which it already
  does). No C-side change needed.

## [0.5.125] - 2026-06-14

**Fix: `wire_delivery_warning` falsely accused the wire when
flow tracking was disabled.**

When the operator turns off Flow Tracking on a stream, netgen
doesn't run an RX sniffer for that stream — rx_rate stays 0 by
design. But the pre-fix `wire_delivery_warning` triggered on
`tx_rate > 100 AND rx_rate < 5% of tx_rate` without checking
WHY rx was zero. Result: every stream with Flow Tracking off
got "wire is dropping ~100% of frames" attached to its stats,
pointing at non-existent switch problems.

Cost: one wasted debug session today where the operator's
tcpdump proved the wire was delivering fine, but the netgen
warning kept insisting the switch was broken.

### Fix

Split the trigger. When `flow_tracking_enabled == false`, emit
a different warning with `reason: "flow_tracking_disabled"` and
a message that points at the Flow Tracking toggle in the dialog
instead of mentioning the switch:

> *"TX is at N pps but RX counter is 0 because Flow Tracking is
> DISABLED for this stream. The wire may be delivering fine —
> netgen just isn't counting. Enable Flow Tracking in the Edit
> Stream dialog to start counting RX packets."*

When `flow_tracking_enabled == true` and rx_rate < 5%, the
original wire-drop warning fires unchanged.

### Tests

`tests/test_v05125_wire_warning_flow_tracking.py` — 6 cases:

* flow_tracking=false + flat rx → reason=flow_tracking_disabled,
  no switch language
* flow_tracking=true + flat rx → original wire-drop warning
* rx ≈ tx → no warning either way
* idle TX (rate < 100) → no warning
* rx_interface unset → no warning
* source code carries the `flow_tracking_disabled` marker
  (regression guard against future refactors)

Full suite: 2577 passed, 1 skipped.

### Lesson for the saga

The wire_delivery_warning was added in v0.5.114 as part of the
srv06 saga's UX layer — to point operators at switch issues
without having to bisect MAC vs VLAN vs storm-control from
scratch. Today it bit the saga itself: the message confidently
named switch storm-control + MAC as the likely cause, the
operator's tcpdump proved that wrong, and the agent (me) wasted
a round chasing a switch-asymmetry diagnosis that didn't exist.

Lesson: any warning that names specific failure causes must
gate on those causes being LIKELY, not just possible. The
diagnostic block here now checks the most basic mode toggle
(Flow Tracking) before suggesting anything else.

## [0.5.124] - 2026-06-14

**VLAN-mode sweep audit — shared `resolve_tx_vlan_id()` helper +
two more "same shape" bugs caught.**

The srv06 saga forced the "respect VLAN:Untagged" check to be
fixed four separate times in adjacent code paths (v0.5.120/121/
122/123). Each fix was correct but the bug shape kept hiding in
a new builder. v0.5.124 centralizes the resolution into one
helper and migrates every TX-side call-site to it — so the next
new TX builder can't accidentally reintroduce the bug.

### New helper

`utils/vlan_helpers.py:resolve_tx_vlan_id(stream_data)` — canon
resolver. Lookup order matches the scapy RX side at
`multithreaded_traffic_gen.py:1199`:

1. `protocol_selection.VLAN` (dialog's live state — winner)
2. top-level `VLAN` (back-compat / API-direct callers)
3. fall through to `vlan_id` if no mode field (legacy pre-v0.4.5)

Returns `None` when untagged or vlan_id invalid; an `int` VID
(1..4094) when tagged. Defensive: never crashes on garbage input.

### Migrated to the helper

| File | Previous state |
|------|----------------|
| `utils/dpdk_tx_worker.py` | inlined since v0.5.121 |
| `utils/generic.py` | inlined since v0.5.123 |
| `utils/uec.py` | **bug**: only checked `vlan_id > 0`, missed mode |
| `utils/rocev2.py` | **bug**: only honored `mode == "Tagged"`, missed Stacked / case mismatch |

`utils/arp.py` uses a separate `vlan_tagged` flag (never set by
the dialog → never tags); not in this audit's scope.
`utils/l2_protocols.py` is L2 emulation, different code path.

### Tests

`tests/test_v05124_vlan_helper_sweep.py` — 16 cases:

* 10 helper-direct (mode resolution, precedence, case
  insensitivity, defensive garbage handling)
* 6 per-call-site (dpdk_tx_worker, generic, uec, rocev2 all
  honor Untagged + Tagged + Stacked correctly via the helper)

Full suite: 2571 passed, 1 skipped.

### Saga summary (final, 9 fixes)

| Version | Real bug |
|---------|----------|
| v0.5.118 | rx_worker stderr capture (the diagnostic that broke the saga open) |
| v0.5.119 | TX pre-launch sweep friendly-fire on rx_worker (`--stream-id` argv collision) |
| v0.5.120 | DPDK tx_worker ignored `VLAN:Untagged` (top-level only) |
| v0.5.121 | DPDK look in protocol_selection too |
| v0.5.122 | RX BPF clamped to UDP for non-DPDK streams via stale dpdk_enable |
| v0.5.123 | scapy TX builder ignored `VLAN:Untagged` (same shape, third surface) |
| v0.5.124 | UEC + RoCEv2 had the same bug shape; shared helper makes it structurally impossible |

Plus the operator-discovered config issue: UEC stream's dst MAC
was wrong (`5c:25:73:3f:30:56` = src MAC) — fixed via the Auto
button shipped in v0.5.112.

## [0.5.123] - 2026-06-14

**Fix: scapy TX ignored VLAN:Untagged mode — third surface of the
same bug shape that's bitten us 3 times now.**

Captured on srv06 via tcpdump: every scapy frame went out
**VLAN-tagged with VID=1, DEI bit set** even though the dialog
showed `VLAN: Untagged`. The QFX5130 access port dropped every
tagged frame → rx_count stayed at 0.

### Root cause

`utils/generic.py:get_packet_config()` read `vlan_id` from
`protocol_data.vlan.vlan_id` with a default of **1** and never
checked the top-level `VLAN` mode field. The downstream
`build_generic_packet()` correctly skips Dot1Q when `vlan_id is
None or vlan_id <= 0`, but the upstream config builder always
fed it a positive integer.

### Fix

`get_packet_config()` now checks `protocol_selection.VLAN` first,
then top-level `VLAN`. If "untagged" → `vlan_ids = [None]`. The
existing guard in `build_generic_packet()` skips the Dot1Q
attach for None vlan_id, so no Dot1Q ever reaches the wire.

Mirrors the same fix shape applied previously:
* **v0.4.5** — scapy RX sub-iface creator
* **v0.5.120** — DPDK tx_worker (top-level lookup)
* **v0.5.121** — DPDK look in protocol_selection
* **v0.5.123** — scapy TX packet builder ← this commit

### Tests

`tests/test_v05123_scapy_tx_vlan_untagged.py` — 9 cases:

* Untagged in protocol_selection → vlan_ids=[None] (the bug fix)
* Untagged + vlan_id=1 (the exact srv06 trip) → [None]
* Tagged → keeps vlan_id (regression guard)
* Stacked → keeps vlan_id
* Top-level VLAN field still respected (back-compat)
* protocol_selection takes precedence over stale top-level
* Missing VLAN field falls through (legacy back-compat)
* Tagged + increment expansion unchanged
* End-to-end: `build_generic_packet()` produces no Dot1Q layer
  when given vlan_id=None

Full suite: 2555 passed, 1 skipped.

### Saga summary so far (8 fixes, all real)

| Version | Real bug |
|---------|----------|
| v0.5.118 | rx_worker stderr capture (the diagnostic that broke the saga open) |
| v0.5.119 | TX pre-launch sweep friendly-fire on rx_worker |
| v0.5.120 | DPDK tx_worker ignored `VLAN:Untagged` (top-level) |
| v0.5.121 | DPDK look in protocol_selection too |
| v0.5.122 | RX BPF clamped to UDP for non-DPDK streams via stale dpdk_enable |
| v0.5.123 | Scapy TX builder ignored `VLAN:Untagged` (same shape, third surface) |

Plus the operator-discovered config issue: UEC stream's dst MAC
was wrong (`5c:25:73:3f:30:56` = src MAC) — fixed via the Auto
button. The pattern of these "same shape, different surface"
fixes argues for a sweep audit pass next: every place that reads
`vlan_id` should check the VLAN mode field. A single shared
helper `resolve_vlan_id(stream_data)` would make this regression
impossible — worth doing in v0.5.124+.

## [0.5.122] - 2026-06-14

**Fix: RX BPF was clamping non-DPDK ICMP/TCP streams to UDP.**

Found on srv06 after the v0.5.121 VLAN-tagging fix landed: two
side-by-side scapy streams with the same dst IP, same untagged
mode, same MACs — only L4 differed. The UDP stream's RX counter
ticked perfectly. The ICMP stream's RX counter stayed at zero.

### Root cause

`multithreaded_traffic_gen.py` decided the RX-side `force_udp` +
`dpdk_hint` based on `should_use_dpdk(stream_data)`, which
returns True whenever the opt-in `dpdk_enable` flag is truthy —
**regardless** of whether the TX side is actually going to run
on DPDK.

The UEC stream had `dpdk_enable=True` lingering from earlier UI
testing but its actual engine was scapy and its L4 was ICMP.
The launcher built `force_udp=True` for the RX side anyway,
which clamped the BPF to UDP-only and silently dropped every
ICMP packet the sniffer saw.

### Fix

Use `resolve_engine()` instead of `should_use_dpdk()`. That's the
same call the TX launcher makes, and it correctly returns
`scapy` when the stream isn't actually compatible with the DPDK
worker (e.g. L4≠UDP, IPv6, multi-protocol). So the RX-side BPF
mirrors what's really on the wire instead of what was originally
requested.

### Tests

`tests/test_v05122_rx_bpf_uses_resolved_engine.py` — 4 cases:

* scapy ICMP + stale `dpdk_enable=True` → BPF builds ICMP (the bug)
* scapy UDP + stale `dpdk_enable=True` → BPF stays UDP (regression guard)
* DPDK UDP → BPF clamps to UDP (original force_udp intent preserved)
* DPDK requested for ICMP (compat rejects) → falls back to scapy
  + ICMP BPF (the second-order safety net)

Full suite: 2546 passed, 1 skipped.

### Saga close-out

This is the 7th fix in the srv06 RX=0 series. The full chain:

| Version | Real bug |
|---------|----------|
| v0.5.118 | rx_worker stderr capture (made everything below diagnosable) |
| v0.5.119 | TX pre-launch sweep friendly-fired rx_worker via shared `--stream-id` argv |
| v0.5.120 | DPDK tx_worker ignored `VLAN: Untagged` mode (top-level lookup) |
| v0.5.121 | VLAN mode actually lives in `protocol_selection`, not top level |
| v0.5.122 | RX BPF clamped to UDP for non-DPDK streams via stale `dpdk_enable` flag |

All real bugs. All test-pinned.

## [0.5.121] - 2026-06-13

**Hot-fix: v0.5.120's VLAN-mode check looked in the wrong place.**

v0.5.120 added a check for `stream_data.get("VLAN")` to respect
the "Untagged" radio. But the Edit-Save path in
`traffic_client/stream_control.py` routes the dialog's VLAN field
into `stream_data["protocol_selection"]["VLAN"]` — because "VLAN"
isn't in the `_TOP_LEVEL_ENGINE_KEYS` promotion list. So the
v0.5.120 check found nothing at the top level and fell through to
the legacy vlan_id path. Result: tx_worker cmdline on srv06 v0.5.120
**still** showed `--vlan 100` after the operator picked "Untagged".

### Fix

Look in `protocol_selection.VLAN` first, then top-level `VLAN`.
Mirrors the scapy code at `multithreaded_traffic_gen.py:1199`
which has always used `(ps.get("VLAN") or stream_data.get("VLAN"))`.

### Tests

`tests/test_v05121_vlan_in_protocol_selection.py` — 6 cases:

* `protocol_selection.VLAN == "Untagged"` drops vlan_id (the fix)
* `protocol_selection.VLAN == "Tagged"` keeps vlan_id
* Top-level VLAN still works (don't regress v0.5.120's lookup)
* protocol_selection takes precedence over a stale top-level VLAN
* Programmatic / API-direct streams without protocol_selection still work
* Truly legacy streams with no VLAN field anywhere fall through to vlan_id

Full suite: 2542 passed, 1 skipped.

### Lessons

The v0.5.120 commit message claimed "THE actual root cause" of the
srv06 saga. It was 90% right — the FIX wired through the wrong
location. Should have grep'd the scapy lookup pattern verbatim
(`ps.get("VLAN")`) instead of inferring from comments. Trust the
working code, not the docstring.

## [0.5.120] - 2026-06-13

**Fix: DPDK tx_worker ignored the `VLAN: Untagged` mode toggle.**

The actual root cause of the srv06 RX=0 saga, after 10 versions
of investigation. The kicker: the SAME bug was fixed on the
scapy side in v0.4.5 — the DPDK path was just never patched.

### Root cause

The stream dialog keeps two independent VLAN fields:

* `VLAN` mode — radio: `Untagged` / `Tagged` / `Stacked`
* `vlan_id` — numeric VID, persisted even in Untagged mode so
  the operator can toggle back without re-typing

`utils/dpdk_tx_worker.py:_resolve_l2_l3_l4()` read only `vlan_id`
and emitted `--vlan` whenever it was non-zero. So a stream whose
mode was "Untagged" with vlan_id still sitting at "100" (the
previous Tagged value) would put VLAN-100-tagged frames on the
wire — and any switch port in access mode silently dropped every
frame.

srv06's QFX5130 port was configured as access. Every test for the
last 10 versions (MAC autopopulate, RX engine outcome surfacing,
NLAT, bifurcated Mellanox, pre-launch sweep friendly-fire,
rx_worker stderr capture) was downstream of this: tx_worker
ALWAYS emitted `--vlan 100`, switch always dropped, RX always 0.

The operator caught it via `ps aux | grep tx_worker` showing
`--vlan 100` after picking "Untagged" in the UI.

### Fix

`utils/dpdk_tx_worker.py:_resolve_l2_l3_l4()` now reads the top-
level `VLAN` field FIRST. If it lowercases to `untagged`, vlan_id
is forced to None regardless of what's in `protocol_data.vlan.
vlan_id`. Other modes (Tagged / Stacked / missing) fall through
to the existing vlan_id resolution.

This mirrors v0.4.5's scapy fix
(`multithreaded_traffic_gen.py:1188`) — both engines now respect
the VLAN mode toggle identically.

### Tests

* `tests/test_v05120_dpdk_vlan_untagged.py` — 7 cases pinning:
  Untagged + vlan_id=100 → None (the bug fix), Tagged + vlan_id=
  100 → 100 (regression guard), Stacked → kept, missing mode →
  falls through (back-compat for old stream JSON), zero vlan_id
  stays None, case-insensitive match, and a symmetry test that
  pins DPDK and scapy to accept the same taggable mode strings.
* Full suite: 2536 passed, 1 skipped.

### Why this is shipped as a fix not a feature

The dialog already correctly persists the `VLAN: Untagged` mode.
The server's DPDK launcher was the only place that ignored it.
Every operator who toggled Tagged → Untagged after v0.4.5
(when the dialog stopped clearing vlan_id) was silently sending
tagged frames from the DPDK engine. Likely many quiet bug
reports we didn't connect to this until srv06 forced a deep
look.

## [0.5.119] - 2026-06-13

**Fix: DPDK pre-launch sweep was friendly-firing the rx_worker.**

v0.5.118's stderr diagnostic on srv06 surfaced the real cause of
the DPDK RX=0 saga. It wasn't bifurcated-Mellanox queue-grab, MAC
mismatch, or switch storm-control — those were red herrings. It
was our own TX-side pre-launch sweep.

### Root cause

`utils/dpdk_tx_worker.py` runs a `pgrep -f` sweep before launching
`tx_worker` to catch orphaned tx_worker processes from a previous
crashed start (would otherwise blast at line rate alongside the
new instance with no telemetry). Pre-fix the regex was just:

```python
_pat = f"--stream-id {stream_id}"
```

That string appears in **both** `tx_worker` and `rx_worker`
cmdlines, because both binaries take the same `--stream-id <uuid>`
argument. So the sweep would find the rx_worker (that the
launcher had spawned ~1 second earlier as part of the same stream
start) and pkill -TERM it as collateral damage.

srv06 captured timeline:
```
T+0.0s  rx_worker spawned, EAL clean, "launched 1 queue worker(s) on port 0"
T+1.0s  TX backend pre-launch sweep: pgrep matches rx_worker by --stream-id
        WARNING: pre-launch: 1 stale tx_worker(s) ... pids=<rx_worker pid>
        pkill -TERM hits rx_worker
T+1.165s  rx_worker exits cleanly (signal handler), exit_code=0, duration=0.165s
T+1.2s  tx_worker actually starts and runs normally
```

Operator saw `rx_pkts=0` because the rx_worker was dead before
any traffic arrived. Scapy fallback worked because Scapy's
sniffer thread doesn't go through this sweep.

### Fix

Anchor the regex on `tx_worker` in the binary path:

```python
_pat = f"tx_worker.*--stream-id {stream_id}"
```

`rx_worker` cmdlines (`/usr/local/bin/rx_worker -l ... -- --stream-id ...`)
never contain the literal `tx_worker`, so the sweep now only
finds real stale tx_workers. Keeps the original orphan-tx-worker
protection intact.

### Tests

`tests/test_v05119_prelaunch_sweep_anchor.py` — 5 cases using
verbatim srv06 cmdlines:

* New pattern STILL matches a real tx_worker cmdline (regression
  guard against over-narrowing)
* New pattern does NOT match an rx_worker cmdline (the bug fix)
* Legacy pattern DOES match rx_worker (pins the bug in test form
  so the lesson survives even if the fix is reverted)
* Source file confirms the anchored pattern is in place
* Sweep for stream A doesn't match stream B's cmdline (UUID
  substring-collision guard)

Full suite: 2529 passed, 1 skipped.

### Notes

* v0.5.118's `stderr_tail` + `exit_code` diagnostic on `/api/admin/
  dpdk/rx/list` is what made this debuggable — without it the
  death looked indistinguishable from a Mellanox EAL crash.
* `project_srv06_rx_worker_blindness` memory is now obsolete (the
  bug wasn't bifurcated-Mellanox specific — it would have killed
  rx_worker on any host). Updating that file separately.

## [0.5.118] - 2026-06-13

**rx_worker stderr capture + post-spawn liveness check — surfaces
the actual death cause of DPDK RX workers instead of leaving the
operator to journalctl.**

Live srv06 state showed the auto-lifecycle path's rx_worker dying
at T+2s with `running=False, latest={}` while the `/api/admin/dpdk/
rx/probe` endpoint worked fine in the same configuration. Death
cause was invisible: `start_rx_worker()` set `stderr=subprocess.
PIPE` but never drained the pipe — a deadlock risk for any worker
that wrote >64 KB of EAL output, and a complete loss of the death
cause for short-lived dying workers (the pipe was discarded on
process exit before anyone read from it).

### Fix

* `utils/dpdk_rx_worker.py`: new `STDERR_TAIL_LINES = 200` constant,
  `RxHandle` gains `_stderr_lines` + `_stderr_reader` fields and a
  `stderr_tail(n)` accessor. `start_rx_worker()` spawns a second
  daemon thread alongside the stdout reader to drain stderr into a
  bounded in-memory ring.
* `utils/dpdk_rx_manager.py`: `list()` surfaces `stderr_tail`
  (last 20 lines) + `exit_code` on each entry when stderr lines
  exist, so `/api/admin/dpdk/rx/list` reports the death cause for
  dead-but-not-yet-reaped handles. Live workers with clean startup
  don't get the field — keeps the common-case response shape
  unchanged.
* `run_tgen_server.py`: `_maybe_start_dpdk_rx_for_stream` does a
  600 ms post-spawn liveness check. If the worker died within
  that window, the outcome's `actual` flips to `scapy` and the
  `reason` field folds in the stderr tail — so `/api/traffic/
  start`'s response carries the actual death cause directly back
  to the operator without requiring a journalctl session.

### Why this matters

On srv06 with `rx_engine=dpdk` selected for a stream, the start
toast previously said "rx_worker spawned (pid 12345)" while the
process was already dead in the background. Operator saw 0 RX
packets and had no surface-level signal pointing at the cause.
v0.5.118 turns that into "rx_worker died within 600ms:
mlx5_common: probe device(0000:2b:00.1) failed | Cannot init pmd"
in the same start response — the actual EAL/PMD bring-up error
makes it to the UI.

This is observability-only. The underlying death cause (likely a
queue-grab conflict with the kernel-bound mlx5 PMD in the
bifurcated Mellanox config; see [memories/project_srv06_rx_worker_blindness.md])
is not addressed here — v0.5.118 captures the failure so the next
operator session on srv06 can see what rx_worker actually says,
which informs whether the real fix is rte_flow rules in
rx_worker.c, a different queue selection strategy, or something
simpler.

### Tests

* `tests/test_v05118_rx_stderr_capture.py` — 6 cases pinning:
  RxHandle gained stderr fields, STDERR_TAIL_LINES bound 50..1000,
  manager `list()` surfaces tail + exit_code when present, manager
  `list()` omits tail for clean live workers, auto-lifecycle
  surfaces stderr in reason on quick death (with assertion that
  actual=scapy + stderr substring appears), auto-lifecycle
  unchanged for healthy worker.
* Full suite: 2524 passed, 1 skipped.

## [0.5.117] - 2026-06-13

**App icon margin fix — matches Apple HIG sizing so the icon
renders at the same visual size as other Dock apps.**

Operator screenshot after v0.5.116 showed the netgen icon
rendering visibly smaller than neighboring Apple-supplied
icons in the macOS Dock — our rounded square filled the full
1024×1024 canvas, while standard Apple icons sit within a
centered ~824×824 area inside the canvas, leaving ~10%
transparent margin on each side. The Dock uses that consistent
margin to align every icon at the same visual size.

### Fix

`scripts/generate_app_icon.py:_draw_icon()` now insets the
icon shape inside the canvas at sizes ≥ 64 px:

* Margin: ~9.8% of canvas (matches Apple's Big Sur template)
* Icon shape: centered at `canvas - 2 * margin`
* Corner radius: 22.4% of icon shape (Apple's squircle ratio)
* Margin area is fully transparent (alpha 0) so the Dock's
  shadow + rounding stay correct

Sizes < 64 px skip the inset — the 1-pixel margin gives up
visible icon area without a perceptible benefit at that scale.

### Regenerated artifacts

* All `resources/icons/netgen-{16…1024}.png`
* `resources/icons/netgen.png` (256 alias)
* `resources/icons/netgen.icns`
* `resources/icons/netgen.ico`

### Tests

`tests/test_v05116_app_icon_wiring.py` gains 2 cases pinning:

* Sizes ≥ 64 px have corner-pixel alpha == 0 (margin present)
* Sizes ≤ 32 px have probe-pixel alpha ≥ 200 (inset skipped)

Full suite: 2518 passed, 1 skipped.

### Migration notes

The PNG / `.icns` / `.ico` containers are regenerated in place
— no consumer-side code changes needed. The next CI build of
the macOS DMG will produce an `.app` whose Dock icon visually
matches Apple's sizing convention.

## [0.5.116] - 2026-06-13

**Speedometer + packet app icon. End-to-end build wiring + v1
rasters so DMG / EXE / AppImage all carry the icon from this
release forward.**

The pre-v0.5.116 builds used the toolbar `add.png` as a
placeholder app icon — a green plus-circle on the dock that did
not communicate the product. v0.5.116 ships a real app icon and
wires every distribution channel to find it by canonical name,
so a future designer pass can replace the rasters without
touching any build scripts.

### Design

Speedometer with a three-segment color arc (gray → blue →
purple, mapping to Scapy → DPDK → RDMA engines in the existing
UI palette), white needle pointing toward the high end, and a
fading purple packet trail across the top. Rounded-square
silhouette in Big Sur style.

Source of truth: `resources/icons/netgen.svg`. PIL-based
generator at `scripts/generate_app_icon.py` rasterizes to all
required sizes + bundles the macOS `.icns` and Windows `.ico`
containers. Re-runnable any time the SVG changes.

### Assets shipped under `resources/icons/`

* `netgen.svg` — source
* `netgen.png` — 256×256 (canonical alias for AppImage)
* `netgen-{16,32,64,128,256,512,1024}.png` — discrete sizes
* `netgen.icns` — macOS bundle (built via /usr/bin/iconutil)
* `netgen.ico` — Windows bundle (6 embedded sizes via PIL)

### Build pipeline wiring

* `ostg_client.spec` → `icon='resources/icons/netgen.icns'`
  (was the `add.png` placeholder)
* `ostg_client_windows.spec` → lookup tuple prefers
  `netgen.ico`, retains older fallbacks for partial-rebuild
  safety
* `build_appimage.sh` → both the embedded PyInstaller spec and
  the AppDir icon lookup point at `netgen.png`
* `run_tgen_client.py` → `QApplication.setWindowIcon(QIcon(
  resources/icons/netgen.png))` — covers PyQt's title-bar /
  alt-tab / taskbar icon (separate from the Dock icon, which
  comes from the `.icns`)

### Tests

`tests/test_v05116_app_icon_wiring.py` (7) — pins:

* All five canonical icon files exist (svg / png / ico / icns /
  netgen-1024.png)
* Every size in the iconset has a corresponding PNG
* macOS spec points at netgen.icns, not add.png
* Windows spec lookup tuple prefers netgen.ico over fallbacks
* AppImage script prefers netgen.png over fallbacks
* run_tgen_client.py wires setWindowIcon
* Generator script references every iconset size

Full suite: 2516 passed, 1 skipped.

### Designer handoff

The SVG is treated as the design source. To replace the v1
icon:

1. Edit `resources/icons/netgen.svg` (or replace with a polished
   redraw at 360×360 viewBox)
2. Update the equivalent drawing code in
   `scripts/generate_app_icon.py` (Pillow doesn't read SVG;
   the script renders the design via Pillow primitives)
3. Run `venv/bin/python scripts/generate_app_icon.py`
4. Commit the regenerated PNGs + `.icns` + `.ico`

No build-script edits needed — file paths are stable.

## [0.5.115] - 2026-06-13

**Statistics tab surfaces the v0.5.114 wire-delivery warning +
What's New entries for the v0.5.110-115 RX saga.**

### Statistics tab — ⚠ on RX cell when wire delivery is broken

The v0.5.114 server-side `wire_delivery_warning` field was
invisible to GUI operators — they'd see rx_count=0 but no hint
about why, same as before. This release wires the warning
through to the per-stream Statistics table:

* RX-count cell prefixes ⚠ when the warning is present
* Cell foreground turns amber (#b45309), distinct from the
  red (#ef4444) "100% loss" indicator — amber wins when both
  apply because the warning IS the explanation for the loss
* Tooltip carries the full summary (named causes) + pointer to
  Help → DPDK Workflow Guide → Troubleshooting

Closes the loop from "server detects the symptom" → "client
operator can act on it".

### What's New entries for the v0.5.110-115 saga

`_FEATURE_GUIDE_HTML` in `widgets/stream_dialog.py` gains a new
section covering the six releases of MAC/RX/switch work:

* Auto-MAC buttons on Source + Destination rows
* Auto-prefill of real iface MACs on Add Stream
* Smart rx_engine default based on the server's per-iface
  advice endpoint (Mellanox bifurcated → Scapy)
* RX-engine telemetry + edit-save engine-key promotion fix
* Wire-delivery warning in stats + Statistics-tab surface
* Pointer to the DPDK Workflow Guide troubleshooting section

Last release before this batch was v0.5.74, so the What's New
was carrying a 41-release gap on user-visible MAC/RX
functionality. Closed.

### Memory consolidation

Three project memory files updated to reflect what actually
shipped (vs. what was tentatively tracked when the saga was
in progress):

* `project_srv06_rx_worker_blindness` — replaced the
  "v0.5.114+ tracking" placeholder with concrete "Status as
  of v0.5.115" listing what shipped + what's still TODO (the
  rte_flow C fix)
* `project_dpdk_blast_template_footgun` — expanded v0.5.113
  mitigation note to cover the full 113-115 chain
* `MEMORY.md` index entries sharpened to <150 chars naming
  shipped fixes + pending work

### Tests

* `tests/test_v05115_wire_delivery_ui.py` (4) — pins the
  client-side renderer contract: wire_delivery_warning
  threaded through all_streams, ⚠ prefix only when warning
  is a truthy dict, tooltip references the DPDK guide, amber
  color used in the RX-cell branch (not just engine column).

Full suite: 2509 passed, 1 skipped.

### Migration notes

* Older clients (pre-v0.5.115) just ignore the new
  `wire_delivery_warning` field — additive change, no breakage.
* v0.5.114 servers + v0.5.115 clients = warnings visible.
  v0.5.115 servers + v0.5.114 clients = warning emitted but
  not rendered (was the v0.5.114 state). Either combo is safe.

## [0.5.114] - 2026-06-13

**Saga close-out — three follow-ups codifying srv06 lessons so
the next operator (or you, tomorrow) finds the answers in the
GUI instead of in chat history.**

### Smart rx_engine default + override warning

New helper `is_mellanox_bifurcated_kernel(iface)` in
`utils/nic_counters.py` detects the specific NIC config that
broke the srv06 saga: `mlx5_core` driver bound, infiniband
devnode present, kernel and DPDK PMD share the chip.

New endpoint `GET /api/interfaces/<iface>/rx_engine_advice`
returns the recommended engine + reason for that iface. On a
bifurcated Mellanox NIC: `recommended: scapy`, reason names
the rx_worker chip-grab + die trap. Standard NICs: `dpdk`.

Dialog wires it in two places:

1. **On Add Stream** (no saved `rx_engine`), the combo defaults
   to whatever the server recommends — no more "the default is
   Scapy and you have to remember to flip to DPDK every time."
2. **On override** (operator picks the non-recommended engine
   anyway), a warning chip appears below the engine combo. Red
   chip when overriding the Mellanox bifurcated recommendation
   (the one that costs hours when missed); yellow chip for
   other mismatches.

Edit path respects explicit saved values — operator's choice
always wins over the advice.

### Wire-delivery warning in stream stats

`/api/streams/stats` now annotates each `active_streams` entry
with a `wire_delivery_warning` field when TX is firing
(≥ 100 pps) but RX is essentially zero (< 5% of TX). The
warning carries the TX/RX rates and a summary naming the three
most likely causes ordered by what we actually hit on srv06:
switch storm-control cap, synthetic source MAC, wrong
destination MAC.

Closes the operator's "is it MAC, VLAN, or switch?" 5-hour
debugging loop. Stats now point at the cause instead of leaving
the operator to bisect.

### In-app DPDK Workflow Guide — section 8 "Troubleshooting"

Added to `widgets/stream_dialog.py:_DPDK_GUIDE_HTML`. Covers:

* MAC autopopulate workflow (Auto button + auto-prefill on
  template apply)
* Mellanox bifurcated rx_engine=scapy workaround with the chip-
  grab mechanism explanation
* Switch storm-control awareness with srv06's measured cap
  table (verified 2026-06-13: ~650 kpps before the QFX5130
  access port starts dropping)
* Port-security violation recovery: shut/no-shut on the switch
  or wait for the violation timer

Accessible from Help → DPDK Workflow Guide or the Read More
button in the stream dialog's Variable Fields tab.

### Tests

* `tests/test_v05114_rx_engine_advice.py` (10) — detector
  contract across NIC families, endpoint shape, dialog
  default-on-add + respect-explicit-on-edit + warning-on-
  override behaviors
* `tests/test_v05114_wire_delivery_warning.py` (7) — detector
  threshold (fires < 5%, skips ≥ 5%), skipped on idle / no-rx-
  iface, endpoint shape preservation

Full suite: 2505 passed, 1 skipped.

### Migration notes

* No config changes required. Existing saved streams keep their
  explicit `rx_engine` values; the smart-default only applies
  to fresh Add Stream.
* The wire-delivery warning is purely additive — old clients
  that don't render `wire_delivery_warning` just ignore the
  field. New clients can surface it in the stream table.
* The proper rx_worker fix (rte_flow rules for selective queue
  steering) is still tracked for a future release; v0.5.114 is
  the workaround-and-documentation layer that lets operators
  use the system safely until then.

## [0.5.113] - 2026-06-13

**Auto-prefill iface MACs on Add Stream + fix dst Auto silent
no-op. Closes the synthetic-MAC footgun the srv06 saga exposed.**

### Bug 1: Dialog defaults synthetic MACs and operators don't notice

The dpdk_blast_e2e template (and several others) shipped with
synthetic MAC defaults like `02:00:00:00:00:01`. The operator
clicked the template, hit Apply, and the wire carried 22 Mpps
of synthetic-MAC frames. On srv06's switch this was enough to
trip port-security and put the port into a state where even
real-MAC frames got dropped — a full switch-port bounce was
needed to recover.

v0.5.110 added an Auto button per row, but it was opt-in. The
operator didn't see a problem until the wire counter was
already flat. By then the switch was already toast.

Fix: `populate_stream_fields` now auto-prefills the Source and
Destination MAC fields from `/api/interfaces/<iface>/mac` when
the field is at a known synthetic default. Per-field gate —
real saved MACs from older streams are not overwritten. Server
unreachable / fetch failure = silent leave-as-is, no crash;
the operator can still click Auto manually or the existing
v0.5.110 mismatch chip will warn them on Apply.

The synthetic-default list covers the all-zero default, the
`02:00:00:00:00:01`-class IANA "locally administered" MACs
that templates use, the historical `aa:bb:cc:dd:ee:01` /
`8c:91:3a:d6:1b:7a` from the test fixtures, and the
`00:00:00:00:00:01`-class trivial counts. Anything else stays
untouched.

### Bug 2: dst Auto button silent no-op when server URL can't resolve

v0.5.112's dst Auto button mirrored the src Auto path EXCEPT
on `_resolve_server_base_for_tx` returning None — src showed a
red error chip, dst silently returned. Operators clicked Auto,
nothing happened, no signal that the click registered. (The
srv06 saga screenshots showed this exact behavior — operator
typed the synthetic MAC manually thinking Auto failed.)

Fix: dst Auto now shows the same red error chip src Auto does
when the server URL can't be resolved. Wording explains the
parent-widget lookup failure and suggests typing the MAC
manually.

### Tests

* `tests/test_v05113_auto_prefill.py` (5) — auto-prefill on
  empty Add, auto-prefill replaces synthetic template MACs,
  real saved MACs are NOT overwritten, server-unreachable
  fallback is graceful, dst Auto shows error chip on
  server-URL-resolve failure.

Full suite: 2488 passed, 1 skipped.

### Migration notes

After upgrading the client to v0.5.113:

* New Add Stream (or template apply) will land in the dialog
  with the real iface MACs already filled in. No clicks
  required, no synthetic-MAC blast possible by accident.
* Existing saved streams with real MACs are untouched — the
  per-field gate only triggers on synthetic defaults.
* If the operator wants to use synthetic MACs deliberately
  (port-security testing, MAC-learning verification), just
  edit the field after open — the auto-prefill only fires
  once, during populate_stream_fields, then it's the
  operator's value.

### Switch-state addendum

The srv06 saga also surfaced that **once a switch port is in
port-security-violation state, no amount of netgen-side fixes
will deliver frames** — neither DPDK nor Scapy, regardless of
MAC. Recovery requires bouncing the switch port or waiting
for the violation timer to age out. v0.5.113 prevents getting
INTO that state in the first place; it does not unstick a
port that's already locked down.

## [0.5.112] - 2026-06-13

**Edit Stream now actually saves rx_engine + Auto button for
Destination MAC.**

The srv06 saga continued past v0.5.111: with the Auto button
working, the operator still saw rx_count=0. Two distinct bugs.

### Bug 1: rx_engine never made it out of the dialog

`stream_control.py:edit_selected_stream` builds its `updated`
dict with a strict list of top-level keys (`stream_id`,
`rx_port`, etc.), then iterates the dialog's edited dict and
routes every OTHER top-level key into `protocol_selection`.
`rx_engine` / `engine` / `dpdk_enable` were never on the
top-level list → got buried at
`stream["protocol_selection"]["rx_engine"]`. But the server's
`_maybe_start_dpdk_rx_for_stream` reads
`stream_data.get("rx_engine")` from the TOP level — saw
nothing, no `rx_worker` spawned, RX engine silently degraded
to Scapy. The dialog said "DPDK (rx_worker)", the saved stream
said "scapy."

Fix: explicit `_TOP_LEVEL_ENGINE_KEYS` tuple — `engine`,
`rx_engine`, `dpdk_enable`, `dpdk_multi_instance`,
`dpdk_tx_cores`, `rx_pci_bdf`, `enable_timestamps`, `rdma` —
promoted to top level before the protocol_selection routing.

### Bug 2: only Source MAC had an Auto button

v0.5.110 added Auto only on Source MAC. Empirically on srv06:
TX with real src MAC + synthetic dst MAC (`00:00:...` or the
template's `02:00:...`) → switch treats as unknown unicast,
floods or drops → ~22% delivery. With both MACs real → ~100%
delivery (verified via direct API call earlier in the saga).

Fix: symmetric Auto button on the Destination MAC row. Reads
the iface name from the RX-port dropdown (canonical "TG N -
Port: <iface>" format; falls back to TX iface when set to
"Same as TX Port") and fetches that iface's burned-in MAC from
the same `/api/interfaces/<iface>/mac` endpoint v0.5.110
shipped.

### Tests

* `tests/test_v05112_engine_propagation.py` (4) — pins
  rx_engine + engine + dpdk_enable top-level survival across
  the edit-save updater; absent keys don't write None at top
  level; non-engine dialog fields still route into
  protocol_selection (no behavioral regression).
* `tests/test_v05112_dst_mac_autopopulate.py` (5) — dst Auto
  button exists, follows RX-port dropdown, falls back to TX
  iface on "Same as TX Port", refuses to write all-zeros MAC,
  iface-name parser handles canonical + bare + legacy formats.

Full suite: 2483 passed, 1 skipped.

### Migration notes

Once srv06 is on v0.5.112, the operator's workflow is:

1. Edit Stream_1 → Protocol Data tab
2. Click **Auto** on Source row → fills `5c:25:73:3f:30:56`
3. Click **Auto** on Destination row → fills
   `5c:25:73:3f:30:57` (since rx_port = ens2f1np1)
4. Stream Control tab → RX engine combo = "DPDK (rx_worker)"
5. Save → Apply → Start

The saved stream will then carry `rx_engine: dpdk` at the top
level, `_maybe_start_dpdk_rx_for_stream` will spawn the worker,
and the switch will route the unicast frames directly to
ens2f1np1 instead of dropping them.

## [0.5.111] - 2026-06-13

**v0.5.110 hot-fix: Auto-MAC button "Could not fetch" error in the
Edit Stream dialog.**

Empirically reproduced on srv06: clicking Auto in the Edit dialog
surfaced "Could not fetch the TX interface's MAC from the server"
even though `GET /api/interfaces/<iface>/mac` worked perfectly when
called directly. Root cause: the Edit Stream flow at
`stream_control.py:1338-1373` transforms each server entry from
`{tg_id, address, online, ...}` to `{tg_id, ports}` before passing
to the dialog (the transform feeds the RX-port dropdown's per-server
iface list). My v0.5.110 helper read `s["address"]` to build the URL
→ None → no fetch.

### Fix

`_resolve_server_base_for_tx` now walks up the Qt parent chain (up
to 8 levels) looking for a widget whose `.server_interfaces` carries
the full shape with `address`. The host StreamControl /
ServerSection mixins keep that shape unmodified, so the dialog
reaches them and resolves the URL correctly.

Tests: 1 new regression case (`test_autopopulate_finds_address_via_parent_chain`)
pinning the parent-chain fallback; all 9 dialog tests + full suite
green.

### Migration notes

Once you upgrade srv06 to v0.5.111, the Auto button works in Edit
Stream the same way it would have in Add Stream. No config change
required.

## [0.5.110] - 2026-06-12

**MAC autopopulate + RX fallback observability — the srv06 RX=0 fix
chain, end to end.**

The Scapy-vs-DPDK controlled test on san-hp-srv06 confirmed the
MAC theory empirically: with the iface's burned-in MAC on the wire
(Scapy via AF_PACKET's kernel rewrite), frames deliver at ~100%;
with the DPDK template's synthetic `02:00:00:00:00:01`, frames
deliver at ~0.05%. The switch between srv06 ports is dropping
synthetic-MAC frames — classic port-security / sticky-MAC behavior.
v0.5.110 ships the operator-facing fix.

### One-click Auto-MAC button in the stream dialog

The Source MAC row in the Protocol Data tab gains an **Auto** button
next to the address field. It hits a new
`GET /api/interfaces/<iface>/mac` endpoint, reads
`/sys/class/net/<iface>/address`, and stuffs the result into
`mac_source_address`. The Source modifier knobs (Increment / Decrement /
Count / Step) are not touched — scaling still steps from the
Auto-populated base.

The endpoint validates the iface name shape (alnum + . _ -, max 15
chars = `IFNAMSIZ-1`) before any sysfs read so a future loosening of
the regex can't reintroduce a path-traversal hole.

Why a button instead of automatic prefill: the dialog opens with the
last-saved MAC (or template default), and silently overwriting it on
load would break flows that legitimately use synthetic MACs (loopback
testing, MAC-learning verification, port-security stress).

### MAC-mismatch warning chip

Below the MAC row, a yellow chip surfaces when the operator picks
DPDK + a source MAC that differs from the TX iface's burned-in MAC.
The chip names the iface MAC inline and offers a clickable
"Use the interface's MAC" link (same effect as the Auto button).

Chip is engine-conditional — it stays hidden under Scapy because the
kernel rewrites the src MAC at AF_PACKET send time anyway, so the
synthetic MAC never reaches the wire. Toggling the TX engine combo
re-evaluates the chip on every flip.

### RX-engine fallback surfaced in the start response

`_maybe_start_dpdk_rx_for_stream` now returns a structured outcome
dict — `{requested, actual, reason, pid}` — instead of just logging
a warning. `/api/traffic/start` folds the outcome into each
`started_streams` entry as `rx_engine_requested`, `rx_engine_actual`,
`rx_engine_fallback_reason`. The client renders a new "DPDK RX
fallback" dialog (separate from the existing TX fallback dialog)
when actual != requested.

Pre-fix the rx_worker spawn outcome only lived in the server log,
which is exactly why the srv06 saga kept asking "is rx_worker even
running?" — there was no UI signal that the spawn was attempted.
Now the operator sees the binary-missing / pci_bdf-unresolvable /
spawn-error reasons inline.

### iface mac_address field on `/api/interfaces`

Same sysfs read is folded into the existing `/api/interfaces`
response so the admin console + dialogs that already poll iface
state can show the burned-in MAC without a second round-trip.

### Tests

* `tests/test_v05110_iface_mac_endpoint.py` (5) — endpoint contract,
  path-traversal rejection, malformed-MAC guard
* `tests/test_v05110_rx_engine_outcome.py` (7) — outcome dict shape
  across every spawn branch + defensive contract (never raises)
* `tests/test_v05110_dialog_autopopulate.py` (8) — Auto button,
  modifier preservation, mismatch chip engine-conditional + branch
  coverage, server-unreachable handling

### Migration notes

No config changes required. Existing saved streams with synthetic
src MACs will trigger the mismatch chip on edit if DPDK is selected
— that's the intended nudge, not a regression.

The autopopulate-vs-scaling tradeoff: scaling on a port-security
switch only works if the operator authorizes the full MAC range on
the switch port. The chip + tooltip flag this; netgen can't change
switch policy.

## [0.5.109] - 2026-06-12

**One-shot RX verdict probe — `POST /api/admin/dpdk/rx/probe`.**

The srv06 saga's final diagnostic step. After the v0.5.107 templates
landed and v0.5.108 fixed the auto-lifecycle crash, the remaining
question is "does the wire actually deliver DPDK-rate packets to RX?"
— which v0.5.107's `dpdk_loopback_check` template was designed to
answer. But that still required the operator to:

1. Start the TX template stream
2. Sleep N seconds
3. Curl `/api/streams/stats`
4. Eyeball `rx_count` vs an expected number
5. Interpret what `hw_imissed > 0` vs `rx_pkts > 0` means

This release collapses that loop into a single API call.

### Endpoint

`POST /api/admin/dpdk/rx/probe` (operator role)

Body:
```json
{
  "rx_iface": "ens2f1np1",
  "rx_pci_bdf": "0000:2b:00.1",   // optional override for vfio-bound
  "duration_s": 10,                // 1..120, default 10
  "expected_pps": 100000,          // drives the verdict
  "vlan": 100,                     // optional filter dims
  "dst_port": 4791,
  "src_port": 1234,
  "src_ip": "10.0.0.1",
  "dst_ip": "10.0.0.2"
}
```

Server-side flow:
1. Resolve PCI BDF (via `iface_to_pci_bdf` or explicit override)
2. Spawn rx_worker with a unique `probe-<uuid>` stream_id (so it
   never collides with an active-stream rx_worker registered by
   the auto-lifecycle helper)
3. Poll `latest()` each second for `duration_s` seconds, collecting
   per-second samples
4. Stop the rx_worker; always cleans up even on exception path
5. Compute verdict and return

### Verdicts

| Verdict | Trigger | Operator action |
|---|---|---|
| `rx_active` | `rx_pkts > 0` and matches `expected_pps` | Wire works — move to line-rate test |
| `rate_limited` | `rx_pkts > 0` but < 50% of `expected_pps × duration` | Switch storm-control / rate-limit (the srv06 line-rate case) |
| `rx_silent` | `rx_pkts = 0` and `hw_imissed = 0` | Cable doesn't deliver; check ACL/VLAN or move to direct loopback |
| `hw_drops` | `hw_imissed > 0` and `rx_pkts = 0` | NIC PMD mempool / queue config — try smaller bursts or fewer queues |
| `needs_diag` | No heartbeat from rx_worker | EAL failure, hugepages missing, etc.; check journal |

Every verdict comes with a human-readable `summary` and an
`actions` array — the operator gets actionable guidance, not just
raw counters.

### Operator workflow on srv06

```bash
# 1. Start the TX stream from a DPDK template (e.g., dpdk_loopback_check)
#    via the client UI — Save + Start

# 2. Hit the probe with the same filter the stream is using:
curl -sX POST localhost:5050/api/admin/dpdk/rx/probe \
  -H 'Content-Type: application/json' \
  -d '{
    "rx_iface": "ens2f1np1",
    "duration_s": 10,
    "expected_pps": 100000,
    "vlan": 100,
    "dst_port": 4791,
    "src_ip": "10.0.0.1",
    "dst_ip": "10.0.0.2"
  }' | jq

# 3. Read verdict + summary + actions
```

Response example (`rate_limited` case):
```json
{
  "verdict": "rate_limited",
  "summary": "Wire delivers but at 12.3% of expected rate
              (123,000 / 1,000,000 packets in 10s). Switch is
              likely rate-limiting unknown-unicast at this pps.",
  "actions": [
    "Check switch storm-control or rate-limit policy on the RX port",
    "Try direct loopback cable to bypass the switch",
    "Lower the TX rate to confirm linear delivery below the cap"
  ],
  "rx_iface": "ens2f1np1",
  "rx_pkts": 123000,
  "effective_pps": 12300.0,
  "delivery_pct": 12.3,
  "hw_imissed": 0,
  "samples": [...]
}
```

### Tests

13 new in `test_v0510x_rx_probe.py`:
- Input validation (missing iface, bad iface, bad BDF, clamped
  duration)
- All five verdict heuristics with mocked rx_worker stdout
- Worker cleanup after every probe (no zombies even after 10x
  in a row)
- 503 when rx_worker binary missing (with install_dpdk.sh hint)
- 400 when PCI BDF unresolvable (with rx_pci_bdf hint)
- Per-second `samples` array exposed in response

Full suite: **2,453 passed**, 1 skipped (+13 new).

### Not yet wired

A "Run probe" button in the admin console's iface drawer next to
the existing "Start rx_worker (60s)" button would be the natural
UI surface. Deferred to v0.5.110 — the endpoint is the substance;
the UI shell is mechanical.

## [0.5.108] - 2026-06-12

**Hot-fix: `_maybe_start_dpdk_rx_for_stream` 500'd on the real
production payload — wrong field-path assumption.**

### Operator-reported symptom (srv06)

After upgrading to v0.5.107 and starting a stream from the new
"DPDK loopback validation" template (or any DPDK stream with
`rx_engine: "dpdk"` set):

```
AttributeError: 'str' object has no attribute 'get'
  at run_tgen_server.py:614, vlan=_int_or_none(vlan_cfg.get("vlan_id"), 0, 4095)
```

`/api/traffic/start` returned 500. Stream never started. Hit any
operator on v0.5.105–v0.5.107 who exercised the auto-lifecycle.

### Root cause

v0.5.105's `_maybe_start_dpdk_rx_for_stream` assumed
`stream_data["L3"]`, `["L4"]`, `["VLAN"]` were dicts holding
per-protocol fields. They're not — they're top-level **string
flags** in the real payload:

```json
"L1": "None",
"VLAN": "Untagged",       ← string, not dict
"L2": "None",
"L3": "IPv4",             ← string, not dict
"L4": "UDP",              ← string, not dict
"protocol_data": {        ← THIS is where the dict lives
    "vlan": {"vlan_id": "1", ...},
    "ipv4": {"ipv4_source": "10.0.0.1", ...},
    "udp": {"udp_destination_port": "4791", ...}
}
```

`.get()` on a string raised AttributeError instantly. The
existing unit test used a fabricated `{"L3": {"dst_ip": "..."}}`
payload shape that doesn't match what the dialog actually
sends, so the bug never surfaced in CI.

### Fix

Field extraction now reads the correct nested path:

| Filter dim | Pre-fix path (broken) | Post-fix path (correct) |
|---|---|---|
| VLAN id | `stream_data["VLAN"]["vlan_id"]` | `protocol_data.vlan.vlan_id` (only when top-level `"VLAN"=="Tagged"`) |
| UDP dst port | `stream_data["L4"]["dst_port"]` | `protocol_data.udp.udp_destination_port` |
| UDP src port | `stream_data["L4"]["src_port"]` | `protocol_data.udp.udp_source_port` |
| IPv4 dst | `stream_data["L3"]["dst_ip"]` | `protocol_data.ipv4.ipv4_destination` |
| IPv4 src | `stream_data["L3"]["src_ip"]` | `protocol_data.ipv4.ipv4_source` |

Defensive: every nested-dict access checks `isinstance(_, dict)`
first and falls back to `{}` so pathological payloads (malformed
client, hand-crafted API call) don't 500 — the helper is always
best-effort.

Also fixed the VLAN logic to **honor the top-level "Tagged" /
"Untagged" flag** — pre-fix, every stream picked up
`protocol_data.vlan.vlan_id=1` (the default field value the
dialog seeds even on untagged streams) and the rx_worker would
filter for VLAN 1, missing every untagged frame on the wire.

### Tests

7 new in `test_v0510x_rx_engine_payload_shape.py`. Test data is a
verbatim slice of srv06's failing payload captured from the
journal — any future refactor of the dialog's payload shape
will fail this test instantly with a clear "you broke the
production contract" diagnostic.

- Captured payload doesn't crash the helper (the reproducer)
- UDP dst/src ports extracted from `protocol_data.udp`
- IPv4 src/dst extracted from `protocol_data.ipv4`
- Untagged stream does NOT pass `--vlan` to rx_worker
- Tagged stream picks up `protocol_data.vlan.vlan_id`
- Missing `protocol_data` doesn't crash (defensive coerce)
- String `protocol_data` doesn't crash (defensive coerce)

Full suite: **2,440 passed**, 1 skipped (+7 new).

### Operator workflow

Upgrade and the templates work as documented in v0.5.107:

```bash
VER=0.5.108
wget https://github.com/amishagrawal2001-arch/netgen/releases/download/v${VER}/ostg_trafficgen-${VER}-py3-none-any.whl
sudo netgen-upgrade ostg_trafficgen-${VER}-py3-none-any.whl
```

Then: Edit/Add stream → Template dropdown → "DPDK loopback
validation · 100 Kpps" → Save → Start. No more 500.

## [0.5.107] - 2026-06-12

**DPDK templates pair TX + RX engines. Two new dedicated templates for end-to-end DPDK.**

The Add/Edit Stream dialog's template dropdown has been a one-click
way to seed sensible stream configs since v0.3.11. But the 15+
DPDK-enabled templates all pre-date v0.5.105's `rx_engine` field —
picking any of them silently defaulted to Scapy RX, dropping at
high pps. Every operator using a DPDK template as a starter would
re-discover srv06's RX=0 saga. Closed.

### Updated existing DPDK templates

All templates with `dpdk_enable: True` now also set
`rx_engine: "dpdk"` so the auto-lifecycle helper spawns rx_worker
on stream start. 15 templates touched:

`udp_line_rate_64b`, `udp_line_rate_1500b`, `udp_imix`,
`lag_hash_test`, `latency_probe`, `vxlan_encap`,
`vlan_tagged_udp`, `mac_dst_sweep_1k`, `mac_src_sweep_1k`,
`mac_src_and_dst_sweep_1k`, `ipv4_dst_sweep_256`,
`ipv4_src_sweep_256`, `ipv6_dst_sweep_64`,
`five_tuple_sweep_rss`, `udp_src_port_sweep_1k`,
`udp_dst_port_sweep_1k`.

### New dedicated templates

**`dpdk_blast_e2e` — "DPDK blast · end-to-end (TX + RX both line-rate)"**

The natural pairing of v0.5.105's tx_worker + rx_worker. Saturates
the wire AND captures every frame on the RX side at hardware
accuracy — kernel netdev bypassed on both sides. Stats surface
`hw_imissed` and `hw_ierrors` so operators see whether the NIC
chip itself is dropping. 128 B frames balance pps stress against
bps headroom. Hardware timestamps on for one-way latency.

**`dpdk_loopback_check` — "DPDK loopback validation · 100 Kpps (safe rate)"**

Same DPDK e2e path as the blast template, throttled to 100,000 pps —
well under any typical switch storm-control cap. Use BEFORE
line-rate tests to confirm the TX→RX wire path actually delivers
DPDK-generated frames.

The summary teaches the diagnostic flow inline:
- RX climbs at 100K but flatlines at line rate → switch is
  rate-limiting unknown-unicast (the srv06 bite)
- RX stays at 0 even at 100K → cable doesn't connect the two ports
  at all; check ACLs / VLAN / direct loopback

### Cross-template invariant

The test suite gains a guardrail: any template with
`dpdk_enable: True` MUST also have `rx_engine: "dpdk"`. Future
maintainers adding a new DPDK template will see the test fail
until they pair the engines. Closes the class of bug that
otherwise reintroduces srv06's RX=0 for every new DPDK template.

### Tests

11 new in `test_v0510x_dpdk_templates.py`:

- Both legacy line-rate templates wire rx_engine correctly
- `dpdk_blast_e2e` exists + has both engines + standard
  MACs/IPs (consistency with sibling templates)
- `dpdk_loopback_check` exists + throttles to ≤1Mpps + uses both
  DPDK engines + summary mentions the srv06 use case
- Cross-template invariant: every `dpdk_enable=True` template has
  `rx_engine="dpdk"`
- New entries are discoverable via `list_templates()` (so the
  dialog dropdown picks them up)

Full suite: **2,433 passed**, 1 skipped (+11 new).

### Operator workflow

```
1. Open client → Add/Edit stream dialog
2. Template dropdown → "DPDK loopback validation · 100 Kpps"
   (or "DPDK blast · end-to-end", or any of the now-fixed
   legacy DPDK templates)
3. Save + Start
4. rx_worker auto-spawns matching the stream's filter
5. /api/streams/stats shows rx_engine=dpdk + hw_imissed/ierrors
6. RX climbs immediately (if the wire works)
```

For srv06's specific debug flow: use **dpdk_loopback_check** first
to confirm the wire delivers AT ALL, then switch to
**dpdk_blast_e2e** for the line-rate validation. If only the
loopback-check template's RX climbs, that's a definitive
"switch is rate-limiting" verdict.

## [0.5.106] - 2026-06-12

**Audit fixes for v0.5.105 — rx_worker leak paths closed.**

Self-review of v0.5.105 found two real defects in the auto-lifecycle
path. v0.5.105's CI shipped clean, but operators heavily exercising
the new Start-Stop-Edit-Start loops would accumulate orphaned
rx_worker processes. This release folds both fixes.

### Bug 1 — Auto-start ordering leak

`_maybe_start_dpdk_rx_for_stream` ran BEFORE the duplicate-stream
checks (by id, by name) and BEFORE the RDMA short-circuit
early-return in `/api/traffic/start`'s per-stream loop. Three
leak paths resulted:

- **Duplicate by stream_id** (operator hits Start twice): rx_worker
  spawned, then `continue` skipped the TX launch. The operator's
  next stop_traffic targets the OTHER instance; this rx_worker
  stays in the registry forever.
- **Duplicate by stream_name** (operator edited a field, hit
  Start again): same.
- **RDMA stream with rx_engine=dpdk**: rx_worker spawned, then
  the RDMA early-return skipped past the helper's stop path.
  rx_worker captures nothing relevant (RDMA is verbs-based, no
  L2/L3/L4 packets on the wire) and runs until `duration_s`
  expires.

**Fix**: moved the call to AFTER both duplicate checks AND after
the RDMA short-circuit. All three paths closed.

### Bug 2 — No atexit shutdown hook

`RxRegistry` holds `RxHandle` references; each wraps a
`subprocess.Popen` child. systemd's default `KillMode=control-group`
reaps these on `systemctl stop`, but:

- Operators sometimes override to `KillMode=process`
- uwsgi / gunicorn deployments don't use systemd at all
- `systemctl restart` has a window where children could linger

**Fix**: `atexit.register(_rx_registry.stop_all)`. Wrapped in
try/except so a corrupt install doesn't crash server startup.

### Tests

7 new in `test_audit_v0510x_rx_leaks.py`:

- Source ordering: auto-start after dup-by-id check
- Source ordering: auto-start after dup-by-name check
- Source ordering: auto-start after RDMA short-circuit return
- atexit hook IS registered + targets the rx manager
- atexit hook is wrapped in try/except (defensive)
- Runtime: `stop_all` actually terminates every registered handle
- Sanity: helper still skips when rx_engine unset (no regression)

Full suite: **2,422 passed**, 1 skipped (+7 new).

### Upgrade path

Same as any v0.5.103+ release:

```bash
VER=0.5.106
wget https://github.com/amishagrawal2001-arch/netgen/releases/download/v${VER}/ostg_trafficgen-${VER}-py3-none-any.whl
sudo netgen-upgrade ostg_trafficgen-${VER}-py3-none-any.whl
```

The self-update + tx_worker/rx_worker rebuild + rlimits drop-in all
fire automatically.

### Known gaps deferred

(Not in scope for this audit-fix release; tracked for v0.5.107+)

- `rx_engine_actual` not in `/api/traffic/start` response (mirror
  of v0.2.75's TX `actual_engine`; needs client toast wiring too)
- Stats fold logs `warning` on every `/api/streams/stats` request
  if manager import fails (need once-per-failure-mode rate limit)
- rx_engine combo visible in stream dialog even when TX engine is
  RDMA (UX polish; not a correctness issue)

## [0.5.105] - 2026-06-12

**DPDK RX: ship the worker, make it usable end-to-end.**

Operator triggered after the srv06 saga revealed kernel netdev RX
overflow at 24M pps (250M cumulative drops, netgen stream rx_count
stuck at 0). This release adds a symmetric DPDK-side RX path so
TX at line rate produces accurate RX counts.

### Phase 1 — The worker itself

**`resources/dpdk/rx_worker/rx_worker.c`** (new, ~370 LOC) — symmetric
to tx_worker:

- Per-lcore RX queue worker (RSS-distributed multi-queue at line rate)
- Per-queue counters, 64-byte aligned (no false sharing)
- Software filter for VLAN / src+dst port / src+dst IP (cheap, post-RX)
- Promiscuous mode auto-enabled so dst-MAC mismatch doesn't drop frames
- JSON heartbeat stdout (one line/sec) + `{"final":true,...}` on exit
- Signal-driven shutdown (SIGINT/SIGTERM) so final summary always emits

**`utils/dpdk_rx_worker.py`** (new, ~250 LOC) — Python launcher
mirroring the tx side:

- `_resolve_rx_worker_bin()` with the same priority chain as tx
- `start_rx_worker(...)` → `RxHandle` (process + stdout reader thread)
- `RxHandle.latest()` / `.final()` — non-blocking snapshot accessors
- `stop_rx_worker()` — SIGTERM → wait → SIGKILL fallback
- Resilient to garbage on stdout (EAL spew, etc.)

**Build pipeline:**
- `pyproject.toml` package-data extended to ship rx_worker sources
- `netgen-upgrade` rebuilds rx_worker after tx_worker (non-fatal:
  if rx build fails, DPDK RX feature is unavailable; Scapy RX still
  works)
- `install_dpdk.sh` Step 6.5 builds rx_worker on fresh installs
  (also non-fatal)

### Phase 2 — Process registry + admin endpoints

**`utils/dpdk_rx_manager.py`** (new, ~150 LOC):
- Thread-safe by-stream_id `RxRegistry` singleton
- Idempotent `stop()` (status=unknown/not_running/stopped)
- Double-start → `ValueError` → HTTP 409
- Reaps dead handles on re-start
- `stop_all()` for clean shutdown

**Flask routes:**
- `POST /api/admin/dpdk/rx/start` (operator) — body: `{stream_id,
  pci_bdf, vlan?, dst_port?, src_port?, src_ip?, dst_ip?,
  rx_queues?, duration_s?, lcores?}` → 200/409/503
- `POST /api/admin/dpdk/rx/stop` (operator) — idempotent
- `GET /api/admin/dpdk/rx/list` (viewer) — all running workers
- `GET /api/admin/dpdk/rx/latest/<stream_id>` (viewer) — one snapshot

Input validated: stream_id matches IFNAMSIZ-like regex; pci_bdf is
strict BDF format; integer args bounded.

### Phase 3 — `/api/streams/stats` folds rx_worker counters

When a `stream_id` has a registered rx_worker, the active_streams
response overrides `rx_count` from worker's `matched_pkts` and
`rx_rate` from `rx_pps`. Hardware drop counters (`hw_imissed`,
`hw_ierrors`, `hw_rx_nombuf`) surface via a new `rx_engine_detail`
object. Adds `rx_engine: "scapy" | "dpdk"` to every stream row.

Fold is best-effort: if the manager crashes, the endpoint still
returns the pre-fold shape (never 500s the stats path because of an
rx_worker bug).

### Phase 4 — Auto-lifecycle: stream start spawns rx_worker

When a stream's `rx_engine` is `"dpdk"`, `/api/traffic/start`
auto-spawns a matching rx_worker via the manager. `/api/traffic/stop`
auto-terminates it. Both directions are best-effort — failure logs a
warning, TX continues, Scapy RX is the fallback.

Helpers `_maybe_start_dpdk_rx_for_stream` and
`_maybe_stop_dpdk_rx_for_stream` live in `run_tgen_server.py`:

- PCI BDF resolution: explicit `rx_pci_bdf` field wins; otherwise
  via `utils.nic_counters.iface_to_pci_bdf` (works for kernel-bound;
  vfio-bound RX needs the explicit override)
- Filter args derived from L3/L4/VLAN sections of stream config
- All 7 failure modes (missing binary, unresolvable BDF, already
  running, etc.) handled with warning logs, never raise

### Phase 5 — Stream dialog RX engine combo

`widgets/stream_dialog.py` gains an "RX:" combo next to the existing
TX engine picker:

- Scapy (kernel sniffer) — default, legacy behavior
- DPDK (rx_worker) — auto-spawns rx_worker on stream start

Saves to `rx_engine` field in stream JSON; restores from saved
streams. Tooltip explains the trade-off (Scapy easy / drops at high
pps; DPDK accurate / needs rx_worker built).

### Phase 6 — Per-iface admin console button

The Live counters tile in the iface drawer gains:

- **▶ Start rx_worker (60s)** button — one-click line-rate
  visibility on the selected iface for 60 seconds (auto-stops)
- **■ Stop** button (visible while running)
- Status line showing worker pid + auto-stop countdown

The JS handler resolves the iface's PCI BDF via
`/api/admin/iface/<iface>/counters` (works for both kernel- and
vfio-bound) before posting to `/api/admin/dpdk/rx/start`. Operator
can spin up rx_worker on ANY iface without touching the CLI.

### Tests

**+62 new tests** across this release:
- `test_dpdk_rx_worker.py` (20) — C source contract + Python
  launcher unit
- `test_dpdk_rx_manager_endpoints.py` (14) — registry +
  admin endpoint integration
- `test_stream_stats_rx_engine.py` (4) — stats fold integration
- `test_rx_worker_e2e.py` (3) — real-subprocess end-to-end
- `test_rx_engine_auto_lifecycle.py` (13) — auto-spawn helpers
  + dialog + admin UI presence
- 1 widening of v0.5.95's drawer-test window (Live counters
  tile growth)

Full suite: **2,415 passed**, 1 skipped (+62 new across the
release).

### Operator workflow

After upgrading to v0.5.105:

**Option A — auto (default for new stream configs):**
1. Open stream config dialog → set RX combo to "DPDK (rx_worker)"
2. Save + Start stream as usual
3. /api/streams/stats automatically shows `rx_engine="dpdk"` +
   accurate counts

**Option B — manual (existing streams):**
```bash
curl -sX POST localhost:5050/api/admin/dpdk/rx/start \
     -H 'Content-Type: application/json' \
     -d '{"stream_id":"udp-test","pci_bdf":"0000:2b:00.1",
          "vlan":100,"dst_port":4791,"duration_s":60}'
```

**Option C — admin console button:**
1. Open admin → click ℹ️ on the RX iface → drawer expands
2. Click **▶ Start rx_worker (60s)** in the Live counters tile
3. Watch RX pps climb live; auto-stops in 60s or click Stop

### Notes

- `rx_worker` requires `vfio-pci` (Intel/Broadcom) or bifurcated PMD
  (Mellanox). Same prereqs as `tx_worker`.
- For Mellanox bifurcated, the kernel netdev stays present alongside
  rx_worker — `tcpdump` / `ethtool` still work on the same port.
- The C binary is rebuilt by `install_dpdk.sh` or
  `netgen-upgrade` against the host's DPDK ABI (avoids the
  `librte_ethdev.so.X` mismatch that bit v0.5.10).
- v0.5.104 features (diagnostic bundle, Live counters tile, RX
  dropped column) are unchanged.

## [0.5.104] - 2026-06-11

**Operator support tooling: diagnostic bundle + live counters for vfio-bound ports.**

After the srv06 saga (v0.5.101 → v0.5.103 took three release-roundtrips
because each round needed one more piece of system state), operator
asked: "DPDK seems flaky — works on one server, breaks on another.
What can we do?" This release ships two operator-support tools that
collapse triage roundtrips and give visibility into the previously-
opaque vfio bind state.

### Feature 1 — One-click diagnostic bundle

**Endpoint:** `GET /api/admin/diag_bundle` (viewer role)
returns a tar.gz of system state.

**Admin UI:** New **Support** card at the top of the admin
console with an **⬇ Export Diagnostics** button.

**Captures** (each section best-effort; missing tools are silent):

| Section | Files |
|---|---|
| system | uname, /proc/cmdline (GRUB IOMMU verify), meminfo, free, cpuinfo |
| packages | dpkg -l filtered to dpdk / mlx / rdma-core / libibverbs / meson / ninja |
| pci | lspci -vvv filtered to Ethernet / InfiniBand controllers |
| dpdk | tx_worker --help (confirms --tx-cores flag landed), stat (build date), per-NUMA hugepages, /mnt/huge findmnt |
| interfaces | ethtool -i / -k / -g / -S per iface + sysfs operstate / carrier / speed / address / mtu + PCI driver symlink |
| firmware | mlxfwmanager --query, ibstat, ibv_devinfo -v |
| systemd | netgen-server unit + drop-ins (proves v0.5.103 rlimits drop-in landed), systemctl show, /proc/&lt;MainPID&gt;/limits (definitive proof of LimitMEMLOCK=infinity) |
| api | 4 JSON snapshots: /api/admin/health, /api/dpdk/status, /api/interfaces, /api/streams/stats (via in-process Flask test_client — no localhost HTTP, no auth roundtrip) |
| journal | netgen-server last hour + dmesg tail filtered to mlx5 / vfio / iommu / dpdk lines |
| netgen | pip show ostg-trafficgen, netgen-upgrade head (confirms which script self-updated) |

**Privacy:**
- Journal is passed through `_redact_journal_secrets` before
  inclusion → tokens scrubbed
- 19 tests guard against `/etc/shadow`, ssh keys, sudoers
- Per-file cap 256 KiB (truncation marker), total 16 MiB
- All tarinfo mtimes = 0 → reproducible, no host-clock leakage
- No MACs / IPs / hostnames in the counter dicts
- Pluggable `journal_redactor` closure; production wires the
  existing redactor

**Resilience:**
- Pluggable `api_fetcher` closure → in-process Flask test_client
  (no HTTP roundtrip, no auth surprises)
- Individual collector crash isolated to `errors/<name>.txt`;
  bundle still ships
- Tail-truncate for log-like commands so the most-recent
  content survives the per-file cap

### Feature 2 — Live RX/TX counters that work for vfio-bound ports

**Module:** `utils/nic_counters.py` (new).

When a NIC is bound to vfio-pci, the kernel netdev disappears.
`ip link show` returns "No such device", `ethtool` and `tcpdump`
have nothing to attach to. Until this release, operators had no
admin-console way to confirm whether DPDK packets were leaving
the wire on a vfio-bound port — the original srv06 RX=0 mystery.

This module exposes a unified counter API that picks the right
source based on the binding state:

| Binding state | NIC vendor | Source path | Action |
|---|---|---|---|
| kernel-bound | any | `/sys/class/net/<iface>/statistics/*` | universal kernel netdev stats |
| vfio-bound | Mellanox ConnectX-4+ | `/sys/class/infiniband/mlx5_N/ports/1/counters/*` | InfiniBand sysfs survives vfio bind because it's at the PCI layer |
| vfio-bound | Intel / Broadcom | n/a yet | returns `source: null` + actionable warning; DPDK telemetry passthrough is v0.5.105 territory |

**Endpoint:** `GET /api/admin/iface/<iface>/counters` (viewer
role; integers + metadata only, no PII surface). Optional
`?pci_bdf=DDDD:BB:DD.F` query for ifaces whose netdev is gone
post-vfio-bind.

**Admin UI:** Click ℹ️ on any iface row → drawer expands → new
**Live counters** tile under the driver header. Polls every 2s
while drawer is open; cleaned up automatically on close. Shows:

- RX/TX pps (delta-computed client-side from two samples)
- RX/TX bps (auto-scaling Kbps / Mbps / Gbps)
- RX packets, TX packets, RX errors, TX dropped (raw cumulative)
- Source label: e.g. `mellanox-sysfs (vfio-bound)`
- Actionable warnings: e.g. `iface ens2f0np0 is vfio-bound;
  counters come from Mellanox InfiniBand sysfs (mlx5_2) —
  kernel netdev path is unavailable while DPDK has the port`

### How this changes srv06 triage

Before this release: a vfio-bound port RX=0 investigation needed
~5 round-trips for me to ask "what's the binding state? lspci?
dmesg? mlxfwmanager?". With these two features:

- One click → tarball with everything support needs (no chat
  roundtrip)
- Open both iface drawers (TX + RX) → see if TX pps climbs and
  RX pps stays at 0 → localize the issue to the wire / cable /
  VLAN filter in 10 seconds without leaving the browser

### Tests

43 new (19 diag bundle + 19 nic_counters + 5 endpoint):
- Bundle: valid gzipped tar, MANIFEST, tolerates missing
  commands, no path traversal, mtimes=0, no /etc/shadow/ssh
  keys/sudoers, caps enforced, tail-truncate keeps log tail,
  api_fetcher + journal_redactor are called, exceptions don't
  kill the bundle, individual collector crash isolated, iface
  filter excludes lo/docker/veth/br-, dpkg filter is network-
  related only
- nic_counters: PCI BDF resolution for kernel + vfio bindings,
  PCI → IB resolution survives vfio, binding detection, kernel
  + Mellanox sysfs readers, top-level source selection, pps
  math (basic / zero-elapsed / None fields / counter reset),
  no PII regex sweep
- Endpoint: shape, iface validation, BDF query validation,
  warning passthrough, exception → 500

Full suite: **2,361 passed**, 1 skipped (+43 new).

### Not in this release

- DPDK telemetry passthrough for Intel/Broadcom DPDK ports
  (v0.5.105 candidate)
- Vendor-aware "Make DPDK Ready" wizard (Mellanox vs Intel
  vs Broadcom branches) — proposed as future work
- Pcap capture from vfio-bound port (would require a DPDK
  rx-tap helper)

## [0.5.103] - 2026-06-11

**netgen-upgrade self-updates from the wheel before doing anything else.**

### Operator-reported symptom (srv06, post-v0.5.102-upgrade, third time)

After running v0.5.102's wheel-upgrade, the next stream-launch
attempt STILL hit:

```
/usr/local/bin/tx_worker: unrecognized option '--tx-cores'
```

But the MEMLOCK fix (also new in v0.5.102) did land — PID
changed, mlx5 PMD probe succeeded. Only the rebuild step
seemed inert.

### Root cause — self-heal timing race

The v0.5.49 self-heal mechanism copies the wheel's bundled
`netgen-upgrade` to `/opt/netgen-server/bin/netgen-upgrade`
**at server startup**, AFTER any in-flight upgrade completes:

```
1. sudo netgen-upgrade <0.5.102-wheel>
2. ↳ OLD netgen-upgrade (pre-v0.5.102) runs
3. ↳ pip install replaces the wheel on disk
4. ↳ systemctl restart netgen-server
5. ↳ Server starts → self-heals netgen-upgrade to v0.5.102
6. Done. Binary stale. v0.5.102's _rebuild_tx_worker only
   takes effect on the NEXT upgrade.
```

So the v0.5.101 → v0.5.102 upgrade ran the v0.5.101 (broken)
script. v0.5.102 → v0.5.103 would run v0.5.102 — but
v0.5.102's rebuild logic still won't fire until the operator
runs another upgrade after the self-heal.

Every release that touches netgen-upgrade hits this same
delayed-effect bite.

### Fix — self-update + re-exec BEFORE main install

`netgen-upgrade` now reads its own bundled copy from the wheel
(via `zipfile`) at startup, compares bytes against
`Path(__file__).read_bytes()`, and if they differ:

1. Writes the wheel's copy atomically (via `.new` + rename).
2. `chmod 0755`.
3. `os.execv` re-executes with the same argv + `--no-self-update`.

The wheel's logic takes effect on the **first** upgrade
attempt, not the second. Idempotent — if bytes match, no
write, no re-exec.

`--no-self-update` prevents infinite re-exec loops in the
unlikely case the wheel's copy still differs after a write
(it shouldn't — bytewise compare).

### Order in `main()` (now)

```
1. _require_root()
2. parse wheel path
3. _maybe_self_update_and_reexec(wheel, argv)  ← NEW
   (everything below this point runs from the wheel's logic)
4. pip install --upgrade <wheel>
5. _verify_imports()
6. _rebuild_tx_worker()
7. _ensure_rlimits_dropin()
8. systemctl restart netgen-server
9. _verify_new_version()
```

### Operator workflow

```bash
sudo netgen-upgrade ostg_trafficgen-0.5.103-py3-none-any.whl 2>&1 | tee /tmp/upgrade.log
```

Watch for:

```
[SELF-UPDATE] wheel's netgen-upgrade differs from /opt/netgen-server/bin/netgen-upgrade; updating and re-executing
[SELF-UPDATE] re-exec: /opt/netgen-server/bin/netgen-upgrade <wheel> --no-self-update
...
[TX_WORKER] ✓ Rebuilt and installed to /usr/local/bin/tx_worker
[RLIMITS] ✓ Wrote /etc/systemd/system/netgen-server.service.d/10-netgen-rlimits.conf
```

On future re-runs of the same wheel:

```
[SELF-UPDATE] ✓ wheel's netgen-upgrade matches running version; no self-update needed
```

### Manual unblock for already-broken srv06 (only needed once)

Before v0.5.103 propagates, the operator can run:

```bash
sudo bash /opt/netgen-server/resources/dpdk/install_dpdk.sh
```

which rebuilds tx_worker (Step 6) and installs to
`/usr/local/bin/tx_worker`. Takes ~30 seconds. From v0.5.103
onward this is no longer needed — the wheel upgrade does it.

### Tests

5 new regression guards in
`tests/test_mellanox_memlock_and_tx_worker_rebuild.py`:

- `_maybe_self_update_and_reexec` is defined
- Reads `resources/tarball/netgen-upgrade` from the wheel zip
- Bytewise compare before replacing self
- Re-exec passes `--no-self-update` to prevent recursion
- Self-update fires BEFORE pip install (so the wheel's logic
  drives every subsequent step)

Full suite: **2,318 passed**, 1 skipped (+5 new).

## [0.5.102] - 2026-06-11

**v0.5.101 bug fix: tx_worker rebuild + Mellanox rlimits via wheel-upgrade.**

### Operator-reported symptom (srv06, post-v0.5.101-upgrade)

`netgen-upgrade ostg_trafficgen-0.5.101-py3-none-any.whl`
completed. PID changed (so restart fired). But on the next
stream-launch attempt:

```
/usr/local/bin/tx_worker: unrecognized option '--tx-cores'
```

— the exact bug v0.5.101's `_rebuild_tx_worker` was supposed
to fix. Binary still stale.

### Root cause #1 — v0.5.101 source-lookup imported a non-package

The v0.5.101 `_rebuild_tx_worker` used:

```python
subprocess.run([str(venv_py), "-c",
    "import resources.dpdk.tx_worker, os; "
    "print(os.path.dirname(resources.dpdk.tx_worker.__file__))"])
```

But `resources/dpdk/tx_worker/` has no `__init__.py` — it's a
data subdir of the `resources.dpdk` Python package, not a
package itself. The wheel ships `tx_worker/tx_worker.c`,
`tx_worker/meson.build`, etc. as package data (see
pyproject.toml `[tool.setuptools.package-data]`), but tx_worker
is NOT an importable Python module.

`import resources.dpdk.tx_worker` raised ModuleNotFoundError,
the `rc.returncode != 0` branch logged
`"could not locate wheel-shipped source; skipping rebuild"`,
and the function returned. The rebuild silently never ran on
any v0.5.101 upgrade — not just srv06.

### Fix #1 — locate the source via the package directory

```python
"import resources.dpdk, os; "
"print(os.path.dirname(resources.dpdk.__file__))"
```

Then join the `tx_worker` subdir and sanity-check
`tx_worker.c` + `meson.build` exist before invoking meson.
If either is missing (theoretical: wheel loses the data
globs), log a clear warning + return. Source-location
success now logs `[TX_WORKER] source located at <path>` so
operators see the rebuild actually progressing.

Skipping or failing the rebuild now uses `logger.warning`
(was `logger.info`) so the line is visible in default-level
upgrade logs.

### Root cause #2 — wheel upgrades never touched the systemd unit

v0.5.101 added `LimitMEMLOCK=infinity` (the Mellanox mlx5 PMD
canonical requirement) to `scripts/tarball/netgen-install`'s
systemd unit template. But that template is only written by
the tarball installer on fresh installs. `netgen-upgrade`
does `pip install <wheel>` + `systemctl restart` — it never
rewrites `/etc/systemd/system/netgen-server.service`.

Pre-v0.5.101 servers upgrading via wheel still hit
`mlx5dv_dr_create_domain failed — Cannot allocate memory`
because the live unit had no LimitMEMLOCK setting (default
~64 KiB locked-memory cap). Operators were forced to manually
drop in the conf, daemon-reload, and restart.

### Fix #2 — drop-in conf via `_ensure_rlimits_dropin`

`netgen-upgrade` now idempotently writes:

```
/etc/systemd/system/netgen-server.service.d/10-netgen-rlimits.conf
```

containing the three Mellanox-required rlimits:

```ini
LimitMEMLOCK=infinity
LimitNOFILE=1048576
LimitNPROC=infinity
```

The `10-` prefix ensures alphabetic precedence before any
operator-named drop-ins. Idempotent: re-running netgen-upgrade
with identical content is a no-op (content-equality check
before write). `systemctl daemon-reload` fires explicitly so
the subsequent `systemctl restart netgen-server` picks up the
new limits.

Order in `main()`:

```
pip install --upgrade <wheel>
_verify_imports()
_rebuild_tx_worker()             # ← now actually rebuilds
_ensure_rlimits_dropin()         # ← new
systemctl restart netgen-server  # ← picks up rlimits + new binary
_verify_new_version()
```

Drop-in MUST fire before restart — otherwise the
freshly-started process would inherit the old (un-dropped-in)
rlimits.

### Operator workflow

For already-installed servers:

```bash
sudo netgen-upgrade ostg_trafficgen-0.5.102-py3-none-any.whl
```

Watch for these lines in the log:

```
[TX_WORKER] source located at /opt/netgen-server/netgen-venv/lib/python3.X/site-packages/resources/dpdk/tx_worker
[TX_WORKER] ✓ Rebuilt and installed to /usr/local/bin/tx_worker
[RLIMITS] ✓ Wrote /etc/systemd/system/netgen-server.service.d/10-netgen-rlimits.conf (Mellanox mlx5 PMD MEMLOCK fix)
```

After upgrade, verify:

```bash
PID=$(systemctl show -p MainPID netgen-server.service --value)
grep 'Max locked memory' /proc/$PID/limits   # expect: unlimited  unlimited
/usr/local/bin/tx_worker --help 2>&1 | grep tx-cores  # expect: [--tx-cores N]
```

### Tests

7 new regression guards added to
`tests/test_mellanox_memlock_and_tx_worker_rebuild.py`:

- v0.5.101 buggy import form must NOT appear in netgen-upgrade
- Fixed form `import resources.dpdk` IS present
- Rebuild sanity-checks tx_worker.c + meson.build exist
- `_ensure_rlimits_dropin` is called from main()
- Drop-in path is canonical (10-netgen-rlimits.conf)
- Drop-in content includes LimitMEMLOCK=infinity
- Drop-in is idempotent (content-equality, mkdir exist_ok,
  explicit daemon-reload)
- Drop-in fires BEFORE systemctl restart

Full suite: **2,313 passed**, 1 skipped (+7 new).

## [0.5.101] - 2026-06-11

**Mellanox MEMLOCK rlimit + netgen-upgrade rebuilds tx_worker.**

### Operator-reported symptom (srv06, Mellanox ConnectX-6 bifurcated)

After upgrading to v0.5.100 (tx_worker resolver SSOT), the very
next UDP DPDK stream launch failed with TWO distinct errors in
the same `journalctl -u netgen-server` excerpt:

```
mlx5_net: ingress mlx5dv_dr_create_domain failed
mlx5_net: probe of PCI device 0000:2b:00.0 aborted —
  Cannot allocate memory
EAL: Bus (pci) probe failed.
/usr/local/bin/tx_worker: unrecognized option '--tx-cores'
```

Stream status: exit code 1, tx_count=0.

### Root cause #1 — systemd unit had no LimitMEMLOCK

The netgen-server.service template in scripts/tarball/netgen-install
set capabilities (v0.5.56 audit H8) but never set rlimits. Default
`ulimit -l` under systemd is ~64 KiB. The Mellanox mlx5 PMD needs
to `mlock()` NIC queue + flow-table memory; without
`LimitMEMLOCK=infinity` the probe bails with `Cannot allocate
memory`. This is the canonical Mellanox DPDK requirement
documented in every NVIDIA / OFED guide.

### Root cause #2 — tx_worker binary drifted from the launcher

`/usr/local/bin/tx_worker` on srv06 was built Jun 8 by the v0.5.99
wheel's install_dpdk.sh Step 6. The v0.5.100 wheel's launcher
(`utils/dpdk_tx_worker.py`) passes `--tx-cores N` for multi-queue
TX scaling. The stale binary has no such flag — `getopt_long`
errors with `unrecognized option`, exit code 1.

netgen-upgrade was only running `pip install --upgrade <wheel>`
then restarting systemd — never rebuilding the tx_worker binary.
Every wheel-style upgrade that touches the tx_worker source could
hit the same drift.

### Fixes

**1. scripts/tarball/netgen-install** — systemd unit template gains:

```ini
LimitMEMLOCK=infinity
LimitNOFILE=1048576
LimitNPROC=infinity
```

with an inline comment quoting the operator-visible error string
so a future `grep -r mlx5dv_dr_create_domain` lands here.

**2. resources/tarball/netgen-upgrade + scripts/tarball/netgen-upgrade**
gain a `_rebuild_tx_worker()` step:

- Fires AFTER pip-install + import-verify, BEFORE the
  `systemctl restart` (so the freshly-started server uses the
  fresh binary on its first stream launch).
- Locates the wheel-shipped source via
  `importlib resources.dpdk.tx_worker`.
- Checks for build deps (`meson`, `ninja`, `pkg-config libdpdk`).
  If missing, logs `[TX_WORKER] build deps missing: …` and
  continues without erroring — Scapy streams still work; operator
  can re-run `install_dpdk.sh` to install deps + rebuild.
- `meson setup` + `meson compile` + `install -m755` to
  `/usr/local/bin/tx_worker` (the canonical resolver path per
  v0.5.99's `_resolve_tx_worker_bin()`).

Both netgen-upgrade copies stay byte-identical
(v0.5.49 self-heal requirement, guarded by
`test_v0549_netgen_upgrade_selfheal.py`).

### Operator workaround for already-installed servers

Pre-v0.5.101 installs need a one-time fix-up:

```bash
sudo tee /etc/systemd/system/netgen-server.service.d/mlx5-rlimits.conf <<'EOF'
[Service]
LimitMEMLOCK=infinity
LimitNOFILE=1048576
LimitNPROC=infinity
EOF
sudo bash /opt/netgen-server/resources/dpdk/install_dpdk.sh   # rebuilds tx_worker
sudo systemctl daemon-reload
sudo systemctl restart netgen-server
```

Fresh v0.5.101 installs (and routine v0.5.101+ wheel upgrades)
get both fixes automatically.

### Tests

`tests/test_mellanox_memlock_and_tx_worker_rebuild.py` — 8 new
regression guards covering:

- `LimitMEMLOCK=infinity` present in unit template
- `LimitNOFILE` bumped to ≥65536
- `LimitNPROC=infinity` present
- Mellanox-bite docstring comment guards the operator-error
  string
- netgen-upgrade calls `_rebuild_tx_worker`
- Rebuild targets `/usr/local/bin/tx_worker`
- Rebuild gracefully skips on missing build deps (warns, returns)
- Rebuild fires BEFORE systemctl restart

Full suite: **2,306 passed**, 1 skipped (+8 new).

## [0.5.100] - 2026-06-11

**tx_worker presence: single source of truth + DPDK Runtime
tile shows resolved path.**

### Operator-reported symptom

UDP DPDK stream on srv06 (Mellanox ConnectX-6 bifurcated)
started and died in under 1 second with `tx_count=0`. Journal:

```
ERROR:dpdk_tx_worker:[dpdk] tx_worker binary not found at
  /opt/netgen-server/resources/dpdk/tx_worker/build/tx_worker
```

But `/api/admin/health` reported `tx_worker.present=true` with
`path=/usr/local/bin/tx_worker`, and `/api/dpdk/status` also
reported `tx_worker_exists=true`. Two presence-checks said yes,
the launcher said no.

### Root cause

Four independent tx_worker candidate lists across the codebase:

| Site | Pre-fix list |
|---|---|
| `/api/dpdk/status` | `/opt/OSTG/`, `/usr/local/bin/`, `./resources/` |
| DPDK verify endpoint | `/opt/OSTG/`, `/usr/local/bin/`, `./resources/` |
| `/api/admin/health` (v0.5.67) | `/usr/local/bin/`, `/opt/netgen/`, `/opt/netgen-server/`, `/opt/OSTG/` |
| `_resolve_tx_worker_bin()` (the launcher) | env, `/opt/netgen/`, `/opt/OSTG/`, wheel, relative, cwd, legacy — **NO `/usr/local/bin/`** |

The launcher was the odd one out. On srv06, `install_dpdk.sh`
Step 6 successfully installed to `/usr/local/bin/tx_worker` and
the three presence-checks confirmed it, but the launcher walked
right past that path and reported "not found".

### Fixes

#### Resolver gains `/usr/local/bin/` at priority 2

`_resolve_tx_worker_bin()` in `utils/dpdk_tx_worker.py` now
checks `/usr/local/bin/tx_worker` immediately after the
`$TX_WORKER_BIN` env override and before the install-dir
candidates. The wheel-shipped fallback (with stale DPDK ABI)
stays last.

`dpdk_tx_worker_multi.py` imports the same resolver, so the
fix covers both single + multi-instance launch paths.

#### Single source of truth — three sites delegate to the resolver

`/api/dpdk/status`, the DPDK verify endpoint, and
`/api/admin/health` now all call `_resolve_tx_worker_bin()`
instead of maintaining their own lists. They retain a small
defensive in-line fallback (that ALSO includes `/usr/local/bin/`
first) for the rare case where the resolver import errors.

Drift class closed: future tx_worker presence reports across
the admin console always match what the launcher will exec.

#### DPDK Runtime tile shows the resolved path

The "tx_worker binary" pill now carries a muted inline path
(` · /usr/local/bin/tx_worker`) on success, OR a red
` · not on any resolver path` hint with a tooltip pointing
at `install_dpdk.sh` Step 6 and the `$TX_WORKER_BIN` env
override on failure.

Operator can now see WHERE the server thinks the binary is
without crawling APIs or the journal.

### Fresh install impact

`install_dpdk.sh` Step 6.2 was always the most reliable step
(`install -m755 /usr/local/bin/tx_worker`). Pre-fix, this
binary was effectively orphaned — the launcher couldn't see
it. Now it's the canonical first-choice install-dir target.
Fresh installs become robust regardless of whether the
`/opt/netgen/...` symlinks succeed.

### Tests

- 4 v0.5.99 follow-up tests for `_resolve_tx_worker_bin()`
  (path present, env override still wins, ordered before
  wheel fallback, docstring).
- 5 new SSOT tests verifying every site delegates to the
  resolver AND defensive fallbacks include `/usr/local/bin/`
  AND the DPDK Runtime tile renders the path hint.
- v0.5.67 admin-health tests updated to verify the new
  delegation pattern.

Full suite: **2,306 passed, 1 skipped** (+9 new this release).

### Operator action

`sudo netgen-upgrade && sudo systemctl restart netgen-server`
on srv06 — the env-var workaround drop-in is no longer
needed (the resolver now finds the binary directly).

## [0.5.99] - 2026-06-11

**Fix: `start_stream` cross-contaminated stream_id across
same-name streams → both streams started together.**

Operator:

> trying to run two streams, when trying to start the selected
> stream both the stream are starting together. check start
> and stop selected stream.

### Two interacting bugs

**Bug 1.** `start_stream` matched the selected row by NAME only:

```python
matched_stream = next(
    (s for s in self.streams.get(port_key, [])
     if s.get("name") == stream_name),
    None
)
```

When two streams on the same port shared a name, `next(...)`
picked the FIRST one — not the operator-selected row. (`stop_stream`
has used the table cell's `UserRole` stream_id since v0.2.84;
`start_stream` was never updated to match.)

**Bug 2.** The sync block that ran AFTER the lookup forced
every same-name stream on the port to take the new stream_id:

```python
for s in self.streams.get(port_key, []):
    if s.get("name") == matched_stream.get("name"):
        s["stream_id"] = stream_id    # cross-contamination
```

With both streams now sharing one stream_id, the stats-poll
path bound BOTH rows to the single server-side stream — both
rows appeared running.

### Fix

- Mirror `stop_stream`'s lookup pattern: read
  `name_item.data(Qt.UserRole)` for the unique stream_id and
  match by that first. Name fallback only when the cell
  carries no stream_id (legacy rows / imported configs).
- Sync ONLY the matched_stream object's identity fields, not
  every same-name sibling on the port.

### Tests

7 new regression tests guarding the stream_id-first lookup,
the gated name fallback, and the no-sibling-cross-contamination
sync.

Full suite: **2,297 passed, 1 skipped** (+7 new).

## [0.5.98] - 2026-06-11

**Admin console audit batch #7 (final): LOW polish.**

Closes the 7-release audit drive that started with v0.5.92.
Consolidates the remaining LOW findings.

### L3 — Duplicate request loggers deleted

Two `@app.before_request` hooks at lines 306 and 402 were both
emitting `[REQUEST] <method> <path>` for every incoming
request — every line appeared twice in journalctl. The
v0.5.92 audit caught this. Deleted the newer pair that added
verbose `[REQUEST DATA]` debug logging (more noise than
signal at info level).

### M13 — `_ethtool_link_fallback` non-timeout errors → warning

Pre-fix `logging.debug(f"[ETHTOOL] ...")` on unexpected
errors. Default log level is INFO, so real OSError / parse
crashes were invisible — operator chasing "no link info"
never saw the underlying cause. Bumped to `logging.warning`.

### UX polish

- **Drawer "Failed:" color** — was raw hex `#b91c1c`. Now
  `var(--bad)` so the error tone matches the rest of the
  admin's red elements.
- **Lifecycle button container** — gains `flex-wrap: wrap`
  so ↑/↓/↻/💡/ℹ️ drops to a second line gracefully at narrow
  viewport widths instead of forcing the action column
  off-screen.
- **Loading flicker fix** — `refreshInterfaces()` now skips
  the `<div class="iface-empty">Loading…</div>` placeholder
  when the table already has rendered content. Pre-fix the
  700ms post-action refresh briefly replaced the populated
  table with "Loading…" — jarring and uninformative since
  the operator already saw the action toast.

### Tests

7 new regression tests for the polish.

Full suite: **2,290 passed, 1 skipped** (+7 new).

### Audit drive summary

| Release | Theme | New tests |
|---|---|---|
| v0.5.92 | Auth + audit trail | +16 |
| v0.5.93 | Cache + race fixes | +10 |
| v0.5.94 | Toast + drawer UX | +8 |
| v0.5.95 | Recovery + integration tests | +12 |
| v0.5.96 | Diagnostic endpoints + caching | +17 |
| v0.5.97 | Operability fortification | +13 |
| v0.5.98 | LOW polish | +7 |

Total: 83 new regression tests across the audit batch.
Suite size: 2,207 → 2,290 (+83).

## [0.5.97] - 2026-06-11

**Admin console audit batch #6: operability fortification.**

### H11 / M9 — Per-iface lifecycle lock

Pre-fix two operators in two browser tabs both clicking Reset
on `ens6np0` could interleave the down→sleep→up sequences.
One operator's `down` could fire between the other's `down`
and `up` — leaving the iface in unexpected states.

New `_iface_lifecycle_lock(iface)` returns a per-iface
`threading.Lock` (dict guarded by `_IFACE_LIFECYCLE_LOCKS_MUTEX`).
Lifecycle handlers `acquire(blocking=False)` — second caller
gets `HTTP 409 + code: IFACE_BUSY` immediately rather than
queueing. Different ifaces still parallelise; only same-iface
contention serialises.

### H12 — `bind_history` survives reboots

Pre-fix `_ADMIN_BIND_HISTORY_PATH = "/tmp/..."` died on every
reboot. The audit trail of who bound what to vfio — the only
persistent record — was lost across `systemctl restart` and
any reboot.

New primary path `/var/lib/netgen-server/admin_bind_history.json`.
`_admin_bind_history_path()` probes writability of the
persistent dir and falls back to `/tmp/` if unwritable (test
runs on dev machines without root). `_load_bind_history()`
reads both locations on startup, preferring the persistent
one.

### L4 — Flash `Popen` reaped immediately

Pre-fix the `subprocess.Popen` for `ethtool -p` was orphaned —
CPython's `subprocess._cleanup()` reaped opportunistically on
subsequent Popen calls, but between calls the zombie sat in
the process table. On a quiet srv06 with one flash click and
no other Popen, the zombie persisted indefinitely.

Now spawn a daemon `threading.Thread(target=p.wait,
daemon=True)` after the Popen — kernel reaps the child as
soon as `ethtool -p` exits.

### Tests

13 new (9 source-level + 2 integration verifying the per-iface
lock blocks same-iface concurrent + lets different-iface
parallelise) + 1 updated (v0.5.95 flash mock gained `.wait()`
to satisfy the new reaper thread).

Full suite: **2,283 passed, 1 skipped** (+13 new).

## [0.5.96] - 2026-06-11

**Admin console audit batch #5: diagnostic endpoints + caching.**

### M10 — `tools_present` in `/api/admin/health`

Pre-fix the admin console only learned `ethtool` / `iproute2` /
`lldpcli` were missing on first action click (e.g. Down → 500
"iproute2 not installed"). Now `/health` surfaces presence of
`ip`, `ethtool`, `lldpcli`, `lspci`, `ibv_devinfo`, `perftest`,
`dpdk-devbind.py` up front. UI can warn before any click.

### M14 — `/api/admin/caches` dump + flush

Operator debugging a stale-cache bug (e.g. the v0.5.87 LLDP
blank-then-fill bite) had to restart the whole service.

- `GET /api/admin/caches` (viewer) — count + ttl + first 30
  keys for ethtool / drvinfo / iface_details / lldp.
- `POST /api/admin/caches/flush` (admin) — drop one
  (`{"which": "ethtool"}`) or all (`{"which": "all"}`).
- Audited via `_admin_audit("caches_flush", ...)` so the
  operator action is recoverable from `/api/admin/journal`.

### M15 — `/api/admin/iface/<n>/sysfs`

Per-iface `/sys/class/net/<n>/` dump exposed via REST. Returns
structured leaves (`address`, `carrier`, `duplex`, `mtu`,
`operstate`, `speed`, `tx_queue_len`, `ifindex`, `type`) plus
a `statistics` dict with every `/statistics/*` counter as int.
Viewer-gated; read-only.

### M2 / SEC M3 — `/details` TTL cache

Each call forked 7 ethtool subprocesses; a tight client loop
could wedge Flask workers. New `_IFACE_DETAILS_CACHE` with 10s
TTL bounds the cost. Cache key is the iface name; verified
under `_IFACE_DETAILS_LOCK`.

### Tests

17 new (12 source-level + 3 integration via Flask test_client +
2 wiring checks). 1 updated (v0.5.91's details-shape check now
recognizes the cached path).

Full suite: **2,270 passed, 1 skipped** (+17 new).

## [0.5.95] - 2026-06-11

**Admin console audit batch #4: recovery + integration tests.**

### H4 — Safety check swallowed stream-tracker errors

Pre-fix `_iface_action_safety_check` had `except Exception:
pass` around `stream_tracker.get_stream_stats()`. If the
tracker was broken, the safety check silently said "no active
streams" — operator could down a NIC carrying live traffic
with no warning.

Fix: fail SAFE. On tracker error, log + return 503
`"stream tracker unavailable, can't verify <iface> is idle.
Use force=true to override."` — operator gets an explicit
signal that the safety system didn't run.

### H5 — `_ethtool_full_dump` mixed errors into stdout

Pre-fix sections were strings like `"(ethtool returned rc=2:
...)"`. Client couldn't distinguish "section unavailable"
from "section legitimately empty".

Fix: each section is now a structured dict
`{"stdout": "...", "error": null}` (or
`{"stdout": "", "error": "ethtool returned rc=2: ..."}`).
JS renderer shows error sections with a muted summary line
and no `<pre>` — failures stand out from data. Backward-
compat preserved (raw string still rendered as stdout).

### H7 — Reset's "down ok / up failed" pointed at SSH

Pre-fix error: `"... Manual: ip link set <n> up."`. That's an
SSH instruction. The operator has the ↑ button right there
in the row.

Fix: new error reads `"<iface> is now down but \`up\` failed:
<stderr>. Click the ↑ button in the row to retry."` plus
structured signals `code: "IFACE_RESET_HALF_DONE"` and
`recoverable_via: "iface_up"` so the JS can offer an action
chip later.

### H8 — Integration tests via Flask test_client

Pre-fix tests were 100% regex-presence — a typo in the JSON
response shape, a regression on `_strict_true`, or a missing
`force` field would all pass. Now exercise the real handlers
with mocked subprocesses:

- `test_iface_up_endpoint_routes` — 200 + correct payload
- `test_iface_down_refuses_without_force_on_default_route` —
  409 with `code: IFACE_DOWN_UNSAFE`, `can_force: true`
- `test_iface_down_force_true_skips_safety_check` — proves
  the safety helper is NOT called when `force=true`
- `test_iface_reset_runs_down_then_up` — verifies the
  sequence order
- `test_iface_reset_down_ok_up_failed_returns_recovery_hint` —
  verifies the new `IFACE_RESET_HALF_DONE` code +
  `recoverable_via` field
- `test_iface_invalid_name_rejected` — regex gate fires
  before subprocess
- `test_iface_flash_endpoint_clamps_seconds` — 999s → 60s

Module-scoped client fixture sets `NETGEN_DB_PATH` to a
tmpdir so module-load doesn't try to `mkdir /opt/netgen` on
dev machines without root.

### Tests

12 new (5 source-level + 7 Flask test_client).

Full suite: **2,253 passed, 1 skipped** (+12 new).

## [0.5.94] - 2026-06-11

**Admin console audit batch #3: toast + drawer UX.**

### H9 — Most error toasts auto-dismissed in 3 seconds

The v0.5.79 sticky-detection regex was `/^(failed:|✗ |⚠ )/i` —
matches the START of the message only. Every natural-language
call site says `toast('X request failed: ' + e)` which starts
with the action verb, not "Failed:" — so the sticky flag was
false and the error vanished in 3s. Operator walked away
thinking the action succeeded.

New regex catches " failed:" / " error[:.]" mid-string too:

```
/(^(failed:|✗ |⚠ |error[:.]))|(\s(failed|error)[:.])/i
```

### H10 — Open ℹ️ Details drawer was destroyed on every refresh

`refreshInterfaces()` replaces `wrap.innerHTML` so the drawer
sibling row disappeared on every 700ms post-action refresh.
The diagnostic tool blew itself away mid-diagnosis — exactly
when an operator was investigating a problem and clicking
↑/↓/↻ on the same row.

Now tracked in `_openIfaceDrawers` (Set keyed on iface name).
`refreshInterfaces()`'s `finally` block re-opens each drawer
after the re-render — instant since the data is in
`_ifaceDetailsCache`.

### M7 — Down/Reset confirm enriched with IPs + streams

Pre-fix: `Bring down ens6np0?`.
Post-fix:

```
Bring down ens6np0?
• 2 IP addresses on this port
• 5 running streams will be disrupted
(reverts on reboot)
```

Mirrors the Bind confirm's enrichment. Operator gets the same
disruption summary regardless of which lifecycle action they're
about to take.

### M8 — `aria-label` on all 5 lifecycle buttons

Pre-fix the glyph-only ↑/↓/↻/💡/ℹ️ buttons were announced as
"up arrow button" by screen readers. Added `aria-label` mirroring
the existing `title` so the announcement carries iface context
("Bring ens6np0 up", "Show full ethtool dump for ens6np0").

### L5 — Force-confirm `\\n\\n` rendered as literal `\n`

v0.5.88's `confirm(\`${data.error}\\n\\nForce ${action} anyway?\`)`
used `\\n` which in the raw-string-served template is a
literal 2-char `\n` text, not a newline. Operator saw the
error and the force-prompt squashed on one line. Fixed to `\n`.

### Tests

8 new regression tests + 1 updated (v0.5.79's toast-window
grown from 1500 → 2500 chars to fit the new comment block).

Full suite: **2,241 passed, 1 skipped** (+8 new).

## [0.5.93] - 2026-06-11

**Admin console audit batch #2: cache + race fixes.**

### H2 — LLDP cache wiped good data on transient lldpcli blip

Pre-fix `_LLDP_CACHE["ts"] = now; _LLDP_CACHE["by_iface"] = {}`
ran BEFORE the `lldpcli -f json show neighbors` subprocess.
On `TimeoutExpired` / non-zero rc the cache was already blanked
— operator saw "(no LLDP)" for every row for the next 30s.
Exact shape of the v0.5.87 srv06 failure.

Build into a local `new_by_iface = {}`. Only commit
`_LLDP_CACHE["by_iface"] = new_by_iface` after successful parse.
On failure, bump `ts` so we don't hammer lldpcli on every
refresh — but leave the prior good neighbor data intact.

### H3 — install_rdma check-then-spawn outside `_ADMIN_INSTALL_LOCK`

v0.5.71 fixed this exact race for install_dpdk; install_rdma
was missed. Two concurrent POSTs could both see `proc is None`
and both `Popen`. State-dict committed by the first; the
second's process orphaned in the dpkg lock queue.

Wrapped the check-then-spawn-then-state-write block in
`_ADMIN_INSTALL_LOCK` (the same lock install_dpdk uses — they
contend on dpkg-lock anyway, so single-lock is correct).
Defense-in-depth re-check before the inner Popen.

### M3 — Stale `_ETHTOOL_CACHE` + `_DRVINFO_CACHE` after lifecycle

Operator clicks Down → 700ms refresh fires → cached carrier=true
+ pre-down speed re-rendered for up to 30s (ethtool TTL) or 60s
(drvinfo TTL). New `_invalidate_iface_caches(iface)` helper
called from up/down/reset on the success path drops both
caches under their respective locks.

### M4 — JS `_ifaceDetailsCache` had no TTL + no invalidation

Once a drawer was opened, every subsequent toggle served the
stale entry forever. After a lifecycle action the operator saw
the pre-action `ethtool` dump indefinitely.

Cache entries now expire after 15 seconds. New
`_invalidateIfaceDetails(name)` is called from the lifecycle
success path so re-opening the drawer after Down/Reset always
re-fetches.

### M5 — `_ADMIN_UPGRADE_STATE` mutated without a lock

Same shape as install_dpdk's pre-v0.5.71 race — missed for the
wheel upgrade path. New `_ADMIN_UPGRADE_LOCK` wraps the initial
check + the Popen + the state-dict update. Two concurrent POSTs
now serialise; the second returns 409.

### Tests

10 new regression tests; updated 1 regex for the LLDP-cache
function-body extraction (early-return + bailout branches
required full-function capture).

Full suite: **2,233 passed, 1 skipped** (+10 new).

## [0.5.92] - 2026-06-11

**Admin console audit batch #1: auth fortification + audit trail.**

From the 4-agent fan-out audit of the admin console. Addresses
the highest-value HIGH/MEDIUM findings; sets up v0.5.93–v0.5.98
for the remaining batches.

### H1 — 4 admin endpoints had no `@require_role` decorator

`/api/admin/health`, `/install_dpdk/log`, `/install_rdma/log`,
`/upgrade_wheel/log` were all anonymously readable in any
auth-enabled deployment because the bearer middleware only
checks "is the token known"; per-endpoint role enforcement is
layered on top via `@require_role`. Added `@require_role("viewer")`
to all four — leaks hostname, kernel cmdline, mounts,
hugepages, install build logs, wheel paths.

### M1 — `bind_history` had stacked `@require_role` decorators

v0.5.80 added `@require_role("viewer")` outside v0.5.68's
`@require_role("admin")`. Python applies decorators bottom-up so
only the innermost gate ever ran; the outer one was dead code.
De-stacked to a single `@require_role("viewer")` and added an
explicit `_role_for_request() == "admin"` branch inside the POST
arm. GET viewable, POST admin — what v0.5.80 thought it was
shipping.

### H6 — Lifecycle/flash/details endpoints had no audit trail

`grep '[ADMIN]'` in v0.5.91 returned **zero** hits across the
whole codebase. v0.5.88-v0.5.91 lifecycle endpoints emitted
nothing beyond the generic `[REQUEST]` per-request line —
operators couldn't reconstruct who reset which iface from
journalctl.

New `_admin_audit(action, iface, **fields)` helper emits one
INFO line per admin mutation:

```
[ADMIN] action=iface_down iface=ens6np0 remote=10.83.6.41
        role=admin force=False rc=ok
```

Wired into all 5 v0.5.88-v0.5.91 endpoints
(up/down/reset/flash/details), every exit branch. `force=`
captured on down/reset so post-incident reconstruction can
distinguish operator-intent from safety-override.

### M11 — `/api/admin/journal` token redaction

The endpoint returned `journalctl -u netgen-server` verbatim.
Two existing `logging.debug(dict(request.headers))` sites in
the ISIS code path would dump `Authorization: Bearer <token>`
into the journal if `OSTG_LOG_LEVEL=DEBUG` is ever set. Added
three scrubber regexes (`Bearer <tok>`, `Authorization:`,
`NETGEN_AUTH_TOKEN[S]=<tok>`) and applied them before returning.
Best-effort + idempotent.

### M12 — GRUB backup `cp` calls had no timeout

Three `subprocess.run(["cp", grub_file, backup_file], check=True)`
calls in the IOMMU config path could hang the Flask worker
forever on a stuck FS / NFS mount. Added `timeout=10` to all
three (initial backup, success-path restore, exception-path
restore).

### SEC L1 — Flash error response leaked `str(e)`

The v0.5.91 flash handler had `jsonify({"error": str(_e)})`
catching the Popen exception. `PermissionError` would leak the
resolved `ethtool` binary path. Changed to return generic
`"failed to start ethtool"` and log full detail server-side.

### Tests

16 new regression tests + 1 updated (v0.5.68's destructive-
route audit now recognizes bind_history's viewer-with-internal-
admin-gate pattern).

Full suite: **2,223 passed, 1 skipped** (+16 new).

## [0.5.91] - 2026-06-10

**Iface table: Diagnostics + ID batch.**

Continuation of the iface-table enhancement drive (the
planned-but-postponed v0.5.89 batch — v0.5.89 and v0.5.90 were
both consumed by hot-fixes).

### Driver / firmware version badge

Each row's "Kernel driver" cell now carries an inline `· fw <ver>`
badge with the firmware version. Hover the badge to see full
`driver / version / firmware` in a tooltip.

Backed by a new `_ethtool_drvinfo()` helper that parses
`ethtool -i <name>` (60s TTL cache). Universal across Mellanox,
Broadcom, AMD-Pensando, Intel kernel drivers.

### Flash LED button (💡)

New per-row button. Click → `POST /api/admin/iface/<name>/flash`
runs `ethtool -p <name> 5` (5 sec by default, body `{"seconds":
int}` overrides 1–60). Spawns via `Popen` so the API returns
immediately while the LED blinks in the background. Standard
ops trick for matching kernel iface name → physical cable.

### Click-row-to-expand drawer (ℹ️)

New per-row button. Click → fetches `GET /api/admin/iface/<name>/
details` and renders a drawer below the clicked row containing
collapsible `<details>` blocks for:

- Link settings (`ethtool <n>`) — open by default
- Driver info (`ethtool -i`)
- Feature flags (`ethtool -k`)
- Interrupt coalescing (`ethtool -c`)
- Ring parameters (`ethtool -g`)
- Driver statistics (`ethtool -S`)
- Permanent MAC (`ethtool -P`)

Each section is best-effort — missing ones report `(ethtool
returned rc=N)` inline. Drawer is cached per iface (in-memory)
so re-toggling is instant.

### Refactoring

- `_RE_IFACE_NAME` moved to module scope (above the helpers that
  validate against it) — earlier callers couldn't see it.
- v0.5.88's duplicate `_RE_IFACE_NAME` in the iface-action
  endpoint section deduplicated.

18 new regression tests.

Full suite: **2,207 passed, 1 skipped** (+18 new).

## [0.5.90] - 2026-06-10

**Hot-fix: full page reload on Up/Down/Reset click.**

Operator on srv06:

> seems entire page is getting refreshed, when trying interface
> related activity up/down/reset... etc.

Root cause: a `<button>` with no explicit `type` defaults to
`type="submit"`. The admin page itself isn't currently wrapped
in a `<form>`, but the default-submit + browser-implementation
quirks can still trigger a full reload depending on what
ancestor handlers exist. Bind/Unbind happens to avoid it; the
new lifecycle buttons hit it.

### Fixes (defense-in-depth)

1. **`type="button"`** added to every Up / Down / Reset
   button in the lifecycle template.
2. **`ev.preventDefault()` + `ev.stopPropagation()`** added to
   the click handler.
3. **Duplicate `style=""` attribute fix.** v0.5.88's disabled
   state emitted `style="..." disabled style="..."` — HTML5
   parser drops the second one silently, so dimmed buttons
   weren't actually dimmed. Collapsed into a single style
   attr that switches base/dim based on carrier state.
4. **`cursor: not-allowed`** on disabled buttons.

5 new regression tests guarding all four.

Full suite: **2,189 passed, 1 skipped** (+5 new).

## [0.5.89] - 2026-06-10

**Hot-fix: TDZ ReferenceError on `link` after v0.5.88 upgrade.**

Operator on srv06:

> post upgrade Error: ReferenceError: Cannot access 'link'
> before initialization

v0.5.88 added a `lifecycleBtn` block for the new Up/Down/Reset
buttons. That block read `link.carrier === true` — but `const
link = i.link || {}` is declared further down in the same row-
render function. JavaScript `const`/`let` has a temporal-dead-
zone: referencing the binding before its declaration line
throws ReferenceError at *runtime*. `node --check` (syntactic)
does NOT catch this — the browser was the first thing to
exercise it.

Fix: read `(i.link || {}).carrier === true` directly inside
the lifecycleBtn block. No dependency on declaration order.
The whole iface table render comes back instantly after
restart.

3 new regression tests guarding the specific pattern.

Full suite: **2,184 passed, 1 skipped** (+3 new).

### Operator action

`sudo netgen-upgrade && sudo systemctl restart netgen-server`,
then reload the admin page.

## [0.5.88] - 2026-06-10

**Interface control: on / off / reset from admin console.**

Operator-requested (Jun 10 2026):

> also allow user to on/off/reset network interfaces from admin
> console

First batch of the iface-table enhancement drive (v0.5.88-v0.5.90).
This release covers **lifecycle control**; v0.5.89 covers
diagnostics+ID (Flash LED, driver/fw, click-row-to-expand);
v0.5.90 covers live ops + structure reshape (sparklines,
auto-refresh, SR-IOV/VLAN/bond nesting).

### Three new admin endpoints

```
POST /api/admin/iface/<name>/up      — ip link set <n> up
POST /api/admin/iface/<name>/down    — ip link set <n> down
POST /api/admin/iface/<name>/reset   — down → 1s sleep → up
```

All `@require_role("admin")`-gated. Iface name validated against
`^[A-Za-z0-9_.-]{1,15}$` (kernel IFNAMSIZ rules) before reaching
`ip link set`. 5s subprocess timeout.

### Safety guard (matches v0.2.76 bind pattern)

`down` and `reset` refuse with HTTP 409 + JSON `{"code":
"IFACE_DOWN_UNSAFE"|"IFACE_RESET_UNSAFE", "can_force": true}`
when the iface:
- carries the host's default route (would kill connectivity)
- carries this SSH session (would kill connectivity)
- has an active stream attached

GUI re-prompts with `"Force action anyway?"`; operator's confirm
re-posts with `{"force": true}`. `up` is harmless so it skips
the check.

### Admin console: per-row Up/Down/Reset buttons

Three compact glyph buttons `↑ ↓ ↻` next to the existing
Bind/Unbind on each row that has a kernel netdev name (vfio-pci
bound rows naturally can't be controlled via `ip link`):

- `↑` Bring up — disabled when carrier already up
- `↓` Bring down — secondary style, disabled when already down,
  prompts confirm
- `↻` Reset (down → 1s → up) — secondary style, prompts confirm

Click handler wraps fetch in try/catch + immediate-feedback
toast (`Bringing down ens6np0…`). Auto-refreshes the iface
table 700ms after the action so the operator sees the link
state update.

14 new regression tests.

Full suite: **2,181 passed, 1 skipped** (+14 new).

## [0.5.87] - 2026-06-10

**LLDP hot-fix (hybrid shape) + collapsible System Info + polish.**

Operator on srv06:

> lldp neighbor is still not seen after upgrading to 5.86, and
> provide collapse view for System info, and Disk(mounted),
> Block Devices, also make it more professional look.

### LLDP hybrid-shape fix

The v0.5.86 `/api/admin/lldp_raw` diagnostic revealed srv06's
lldpd emits a THIRD shape neither v0.5.82 nor v0.5.86 caught:

```json
"interface": [
  {"ens10f0":   {"chassis": ..., "port": ...}},
  {"ens2f0np0": {"chassis": ..., "port": ...}}
]
```

A **list** where each element is a **single-key dict** with the
iface name as the key. v0.5.86's list branch did
`entry.get("name")` which returned None on every row → silent
"(no LLDP)" everywhere.

Fix: when a list element is a single-key dict and the key isn't
a known entry field (`name/chassis/port/via/rid/age/ttl`), treat
the key as iface name and the value as the entry.

After this fix srv06 will show:
- `ens10f0` → sd-mgmt-a22.englab.juniper.net · ge-0/0/12 · 10.83.38.209
- `ens2f0np0` → ny-q5130-03.englab.juniper.net · et-0/0/29:1 · 10.83.6.63
- plus the others reported in the journal dump.

### Collapsible System Info

Three native `<details>` blocks for Host hardware / Disks
(mounted) / Block devices. "Host hardware" is open by default;
the two disk tables collapse. Each summary line carries a one-
line meta hint:

```
▾ Host hardware            64 cores · 252 GiB RAM · Ubuntu
▸ Disks (mounted)          3 mounts
▸ Block devices            4 disks, 14 devices
```

Custom CSS (`.collapse`) hides the native disclosure triangle
and uses a rotating chevron, hover background, slate-700 text
when open. No JS needed — `<details>` is the native pattern.

### Professional polish

* Card H2 gets a faint bottom border (`#f1f5f9`) for a clearer
  section line.
* Card H2 text → slate-700 (`#1e293b`); H3 → slate-600 (`#334155`).
* H1 18 → 19 px, -0.2px letter-spacing for a more deliberate feel.
* Cards: extra subtle second-layer shadow for depth.
* Grid: 10 → 12 px gap.
* Card padding: 10/12 → 12/14.

7 new regression tests including the hybrid-shape round-trip.

Full suite: **2,167 passed, 1 skipped** (+7 new).

## [0.5.86] - 2026-06-10

**LLDP parser handles json0 shape (Juniper) + all block devices.**

Operator on srv06 after upgrading to v0.5.85:

> i am not seeing lldp neighbor on the admin console, however i
> can see on server
>
> [paste of qfx5130-32cd neighbor on ens2f1np1]

And:

> also i want to see all the disks available on the server and
> usage. [paste of fdisk showing /dev/sda1..3]

### LLDP parser fix

The v0.5.82 parser only handled lldpd's older `json` shape (array
of interfaces with a "name" field). srv06's lldpd talking to a
Juniper qfx5130 emits the newer `json0` shape:

```json
{
  "lldp": {
    "interface": {
      "ens2f1np1": {                           ← iface name as KEY
        "via": "LLDP",
        "chassis": {
          "ny-q5130-03.englab.juniper.net": {  ← sys-name as KEY
            "id": {"type": "mac",
                   "value": "20:ed:47:10:b4:7d"},  ← typed-dict id
            "descr": "Juniper Networks qfx5130-32cd",
            "mgmt-ip": ["10.83.6.63"]
          }
        },
        "port": {
          "id": {"type": "local", "value": "689"},
          "descr": "et-0/0/29:0"
        }
      }
    }
  }
}
```

Three new helpers handle both shapes:

* `_lldp_extract_id_value(node)` — bare string OR `{type, value}` typed dict
* `_lldp_normalise_chassis(node)` — list shape OR json0 sys-name-as-key
* `_lldp_normalise_port(node)` — list shape OR json0 typed-id

`_refresh_lldp_cache` now walks both shapes via a single
`(name, entry)` pair list. Heuristic: if a dict's keys aren't
in the known entry-field set (`name/chassis/port/via/rid/...`),
treat them as iface name → entry mapping.

### Diagnostic: `GET /api/admin/lldp_raw`

Returns raw `lldpcli -f json show neighbors` stdout. So next time
a different lldpd version emits an unexpected shape, the operator
can hit the endpoint from the browser to inspect — no SSH needed.

### Block devices in admin dashboard

`/api/admin/health` `disk` block gains `block_devices` (list of
all real disks + partitions from `lsblk -J -b`):

```json
"block_devices": [
  {"name": "sda", "parent": null, "type": "disk",
   "size_bytes": 999898038272, "fstype": "",
   "mountpoint": "", "model": "INTEL SSDSCKKB", "serial": "BTYS..."},
  {"name": "sda1", "parent": "sda", "type": "part",
   "size_bytes": 1127219200, "fstype": "vfat",
   "mountpoint": "/boot/efi", "model": "", "serial": ""},
  ...
]
```

* Skips loop + ramdisks (operator cares about real disks).
* Capped at 64 entries.

Admin HTML adds a "Block devices" table under the existing
"Disks (mounted)" table. Partitions indented under their parent
disk; sizes auto-formatted as GiB/TiB. The Disks (mounted) table
shows utilization; Block devices shows the full inventory.

11 new regression tests including a Juniper-shape parser round-trip.

Full suite: **2,160 passed, 1 skipped** (+11 new).

## [0.5.85] - 2026-06-10

**Hot-fix: /api/admin/health 500 + System Info card at top.**
Operator on srv06:

> missing system info, and also move the system info on the
> top , also seeing Loading... on top.

### Root cause (found via dogfooded /api/admin/journal)

```
File "run_tgen_server.py", line 16821, in api_admin_health
TypeError: list indices must be integers or slices, not str
```

v0.5.84 added `disk["mounts"] = [...]` (a list) to enumerate
real filesystems. The pre-existing disk-warnings loop iterated
ALL of `disk.items()` and did `_info["free_mb"]` on each value
— fine for the three named dict entries, but `_info["free_mb"]`
on the mounts list raised TypeError → entire health endpoint
500'd → admin console JS silently failed on the first field
assignment → hostname stayed at "Loading…", System Info card
stayed at "…" placeholders, all the other pills froze too.

### Fix

* `for _label, _info in disk.items()` now skips non-dict entries
  via `isinstance(_info, dict)`. The mounts list has its own
  per-row used_pct surfaced through the disks table render.
* System Info card relocated to be the **first** card in the
  grid (operator-requested in the same message).
* Full suite: **2,149 passed, 1 skipped** (+4 regression tests).

### Why dogfooding paid off

This was found in ~3 minutes via the v0.5.80
`/api/admin/journal` endpoint — no SSH to srv06 required. The
3-line traceback was enough to point at the exact line.

## [0.5.84] - 2026-06-10

**Admin dashboard: System Info card, Mellanox link-speed fix,
compact CSS.** Three operator-requested changes from a single
"go" session — combined here on release.

Full suite: **2,145 passed, 1 skipped** (+12 new tests).

### 1. Mellanox link-speed fix

Operator on srv06:

> i also see some interface are not showing speed and some are,
> ens6np0 ... ConnectX-7 ... DPDK-ready ... [no speed shown],
> ens2f1np1 ... ConnectX-6 ... [↑ 200 Gb/s full]

Probe confirmed `_get_link_info` returned `carrier: None +
speed_mbps: None` for srv06's ConnectX-7 ports while ConnectX-6
ports came through fine. Root cause: sysfs
`/sys/class/net/<n>/carrier` returns EINVAL (raises `OSError` on
read) when `operstate=unknown` — common with mlx5 ports in
some bonding / SR-IOV / IB-attached configurations.

Fix:
* Per-file `OSError` catches (not bare `Exception`) — one
  failure no longer blanks the whole struct.
* New `_ethtool_link_fallback(iface)` runs `ethtool <iface>`,
  parses Link / Speed / Duplex, 30s TTL cache, 2s subprocess
  timeout, `FileNotFoundError`-tolerant.
* `_get_link_info` reads `operstate` first (always populated),
  then falls back to ethtool for any field sysfs left `None`.
* Admin JS treats `carrier=null + speed-present` as "link up"
  (so the ethtool fallback actually surfaces) and adds a
  "link state unknown" branch for the rare cases where both
  sysfs and ethtool come up empty.

### 2. System Info card

Operator:

> pls include additional information about the server in admin
> dashboard, like total memory/free, total cpu/cores, total disk
> and space/free per disk... etc

`/api/admin/health` gains `system` block + extends `disk`:

```json
"system": {
  "kernel": "5.15.0-105-generic",
  "distro": "Ubuntu 22.04.4 LTS",
  "arch": "x86_64",
  "host_uptime_sec": 1056382,
  "cpu": {
    "model": "Intel(R) Xeon(R) Gold 6346",
    "cores_physical": 32,
    "cores_logical": 64,
    "load_avg": [0.42, 0.38, 0.34]
  },
  "memory": {
    "total_mb": 258512,
    "free_mb": 163277,
    "available_mb": 169045,
    "used_mb": 89467,
    "buffers_mb": 1820,
    "cached_mb": 28432,
    "swap_total_mb": 8192,
    "swap_free_mb": 8192
  }
},
"disk": {
  "tmp": {...},
  "var_lib_netgen": {...},
  "opt_netgen": {...},
  "mounts": [
    {"mountpoint": "/", "device": "/dev/nvme0n1p2",
     "fstype": "ext4", "total_mb": 102400,
     "free_mb": 62100, "used_pct": 39},
    ...
  ]
}
```

* CPU model + cores from `/proc/cpuinfo` (physical = sockets ×
  cores-per-socket; logical = `processor :` line count).
* Memory from `/proc/meminfo` (total/free/available/used/buffers/
  cached/swap).
* Kernel/distro/arch from `platform` + `/etc/os-release`.
* Host uptime from `/proc/uptime`.
* Disk mounts enumerated from `/proc/mounts`. Skips pseudo-FS
  (`tmpfs`, `proc`, `sysfs`, `overlay`, `cgroup*`, ...) and
  docker container mounts. Each row gets
  mountpoint/device/fstype/total_mb/free_mb/used_pct. Sorted
  largest-first, capped at 16 entries.

Admin HTML adds a **System Info** card with three column groups
(Host / CPU / Memory) plus a Disks table; rendered with
human-readable units (GiB / MiB / d h m), `used_pct` colored
amber ≥85%, red ≥95%.

### 3. Compact admin CSS

Operator screenshot + "make it compact". Tightened across the
board — roughly 30% shorter dashboard without losing any data:

| Element | Before | After |
|---|---|---|
| H1 | 22 px | 18 px |
| Card padding | 16/18 | 10/12 |
| Card H2 | 15 px, mb 10 | 13 px, mb 6 |
| Row padding | 6 px | 3 px |
| Pill | 2/8, 11 px | 1/7, 10 px |
| Button | 8/14, 13 px | 5/11, 12 px |
| Iface table padding | 8/10 | 4/8 |
| Grid gap | 16 px | 10 px |
| Min column | 360 px | 320 px |

12 new regression tests guarding the link fallback + system
info shape.

## [0.5.83] - 2026-06-10

**Help → Install Guide updated for v0.5.82 LLDP additions.**
Operator-requested:

> update the netgen server installation guide under help menu

Three updates to `widgets/stream_dialog.py`'s
`_INSTALL_GUIDE_HTML`:

* **Disk footprint table** gets a new `/etc/lldpd.d/netgen-server.conf`
  + `lldpd` package row, marked v0.5.82 — operators reading the
  guide know what footprint to expect.
* **"What v0.5.x install does NOT touch"** bullet list — the
  "No apt packages installed" claim is qualified with the
  v0.5.82 exception (single non-fatal `apt install -y lldpd`).
  Stops the guide from misleading anyone diff-ing fresh installs
  against the docs.
* **New "LLDP neighbor discovery" subsection** explains what
  lldpd does, what the admin console renders (`sys_name` /
  port descr / mgmt IP per row, full chassis details in
  tooltip), and how existing servers pick lldpd up (Make DPDK
  Ready apt-deps OR manual `apt install -y lldpd`).
* **Expected end-of-log success snippet** gets the LLDP install
  lines and bumps the verify-OK version to 0.5.82.

5 new regression tests guarding the documentation edits.

Full suite: **2,133 passed, 1 skipped** (+5 new).

## [0.5.82] - 2026-06-10

**LLDP neighbor in admin iface table + lldpd in fresh installer.**
Operator-requested:

> also enable lldp on the server and lldp package should be part
> of tar.tgz fresh installer. also allow admin console to see the
> lldp neighbor in the network interface table

Three additions:

### 1. Fresh installer (`scripts/tarball/netgen-install`)

New `_setup_lldpd()` step runs between preflight and FRR-image
build:
- `apt-get install -y lldpd` (only apt-based step in the installer
  — justified because lldpd is universally available, tiny, and
  the alternative is a multi-step shoulder-tap operators don't
  want)
- Writes `/etc/lldpd.d/netgen-server.conf` with `tx-interval 30`,
  `tx-hold 4`, sets a friendly platform/system-descr
- `systemctl enable --now lldpd`
- Best-effort: failure logs a warning + continues — does NOT
  abort the install

### 2. Existing servers (`resources/dpdk/install_dpdk.sh`)

Added `lldpd` to the apt-deps list. Servers running "Make DPDK
Ready" pick it up automatically. No separate dance for srv06.

### 3. Backend + admin console

* `_LLDP_CACHE` (30s TTL, threading.Lock-guarded) caches the
  parsed `lldpcli show neighbors -f json` output sliced per-iface
  — one lldpcli fork per ~30s instead of N forks per refresh.
* `_refresh_lldp_cache()` runs lldpcli with 4s timeout, parses
  the JSON (handles both flat and wrapped shapes across lldpd
  versions), catches `FileNotFoundError` so the iface table
  still renders on hosts without lldpcli.
* `/api/dpdk/interfaces` items get a new `lldp_neighbor` field:

```json
"lldp_neighbor": {
  "sys_name": "leaf-sw-01",
  "sys_descr": "Arista EOS 4.30.5M",
  "chassis_id": "fc:bd:67:..",
  "port_id": "Ethernet5/1",
  "port_descr": "to:srv06 ens3f0",
  "mgmt_ips": ["10.0.0.1"]
}
```

* New **LLDP neighbor** column in the iface table between IP
  addresses and TX queues:

| LLDP neighbor |
|---|
| **leaf-sw-01**<br><sub>Ethernet5/1 · 10.0.0.1</sub> |
| — |

Hover for full chassis ID + sys descr + port descr + all mgmt IPs.

Full suite: **2,128 passed, 1 skipped** (+14 new tests).

### Operator action

- **Fresh installs**: just untar + run installer; lldpd handled.
- **srv06 (existing)**: wheel-upgrade to v0.5.82 + either
  re-run "Make DPDK Ready" (gets lldpd via apt-deps), OR
  `apt install -y lldpd && systemctl enable --now lldpd`
  manually + restart netgen-server.

## [0.5.81] - 2026-06-09

**Hot-fix: admin console stuck on "Loading…" (JS SyntaxError).**
Operator on srv06:

> failing to load admin console and stuck in Loading…

### Root cause

v0.5.80's accelerators empty-state used a single-quoted JS string
containing the English contraction `don't`. The Python source had
backslash-apostrophe written as `\\'` to escape both backslash and
apostrophe. Python's regular triple-quoted string then collapsed
`\\` → `\`, so the JS source contained `don\\'t`. JS parsed `\\`
as one literal backslash and the following `'` terminated the
string prematurely:

```
SyntaxError: Unexpected identifier 't'
```

→ entire admin JS dead → all pills/banners/iface table stuck on
the initial "Loading…" placeholders. No console errors visible
because the script tag never ran past the SyntaxError.

### Fix

* Accelerators empty-state innerHTML now uses a backtick template
  literal and reworded to "do not" to remove the apostrophe
  entirely — belt and suspenders.
* v0.5.75's NO_PMD tooltip (template literal but had the same
  `\\'` pattern) cleaned up to plain apostrophe.

### Regression guard

New tests/test_v0581_admin_js_syntax_guard.py scans the entire
file for `\\'` patterns (the canonical trap) and fails CI if any
appear.

Full suite: **2,114 passed, 1 skipped** (+3 new tests).

### Operator action

Wheel-upgrade srv06 to v0.5.81 + restart netgen-server. Admin
console will populate normally.

## [0.5.80] - 2026-06-09

**Close-out batch — journal endpoint, viewer auth, PMD flag,
accel pagination, empty-state.** Final batch of the "go" drive.
Six audit findings closed.

Full suite: **2,111 passed, 1 skipped** (+10 new tests).

* **MEDIUM #11** — `GET /api/admin/journal?lines=N` tails the
  netgen-server journal. Cap 500 lines, 5s timeout,
  `@require_role("viewer")` gated. Operator no longer needs SSH
  for post-mortems.
* **MEDIUM endpoint #5** — viewer-tier `@require_role` added to
  `/api/admin/interface_ips` GET and `/api/admin/bind_history`
  GET. Pre-fix unauthenticated viewers could dump per-iface IPs +
  PCI topology (the POST paths were already admin-gated).
* **LOW endpoint #12** — `_NO_PMD_DRIVERS` hoisted to module
  scope from inside `/api/dpdk/bind`. New `pmd_supported`
  boolean per iface in `/api/dpdk/interfaces` — GUI can
  preemptively flag bind-incompatible rows without round-tripping
  the 409.
* **LOW endpoint #9** — `/api/dpdk/accelerators` paginates at
  256 entries; response includes `truncated` + `cap`.
* **LOW #13** — accelerators JS empty-state differentiation. On
  AMD CPUs (no ioatdma/idxd) the card now shows: *"No DPDK
  accelerators detected on this host. Intel I/OAT DMA and DSA
  are CPU-internal — AMD CPUs and most non-Xeon Intel CPUs
  don't expose them."* Fetch failures still hide the card +
  `console.warn`.

### "go" drive recap (v0.5.78–v0.5.80, 22 findings)

Started after the operator asked for link status + further gaps.
All 27 audit findings now closed:

| | Found | Shipped |
|---|---|---|
| HIGH | 5 | 5 ✓ |
| MEDIUM | 11 | 11 ✓ |
| LOW | 11 | 11 ✓ |

## [0.5.79] - 2026-06-09

**Toast queue, enriched bind confirm, IP modal a11y.** Three
MEDIUM audit findings closed. Full suite: **2,101 passed, 1
skipped** (+11 new tests).

* **MEDIUM #5** — toast queue: pre-fix toast() used a single
  DOM node with one clearTimeout; two rapid calls overwrote
  each other before the operator could read the first.
  Stacked container at bottom-right, each call creates a child
  node. Sticky red for failure-leading messages
  (`Failed:` / `✗ ` / `⚠ `), auto-dismissed after 3s for
  success.
* **MEDIUM #12** — bind confirm enrichment. Pre-fix the only
  warning was "It will become invisible to the kernel". Now
  includes "N IP addresses configured — will vanish" + "M
  running streams — will be disrupted" when applicable.
* **MEDIUM #7** — IP modal a11y: Escape closes, Tab cycles
  inside, focus restored to opener on close, input
  auto-focused on open.

## [0.5.78] - 2026-06-09

**Service card + disk + connection-lost + driver allowlist + PCI
tighten + hugepages bounds.** Continuing the "go" drive. 6 audit
findings closed in one release.

Full suite: **2,090 passed, 1 skipped** (+14 new tests).

* **HIGH #4** — netgen-server service card. Pre-fix the admin
  console showed nothing about the systemd unit serving it.
  Now: `Service / PID-RSS / Uptime / Disk free` row pills via
  `systemctl show` + `/proc/<pid>/stat`. Disk row colors warn
  <1 GB, bad <100 MB.
* **MEDIUM #6** — connection-lost banner. Tracks
  `_lastHealthOkMs`; if stale >90s (3× the 30s poll) a sticky
  red banner appears: `⚠ Connection lost — last update Xm Ys ago.
  Reconnecting…`
* **MEDIUM #9** — disk-space in `/api/admin/health`:
  `disk: {tmp, var_lib_netgen, opt_netgen}` each with `free_mb` +
  `total_mb`. Issues entries at <1 GB / <100 MB free.
* **HIGH #3** — driver-name allowlist regex
  `^[A-Za-z0-9_-]{1,32}$` before label-string interpolation in
  JS. Defense-in-depth against unusual sysfs symlink values
  reaching the iface table render.
* **LOW endpoint #8** — iface-table PCI class filter tightened
  from `startswith("02")` to `{0200, 0207}`. Excludes the rare
  class 0280 ("other network controller", sometimes onboard
  management chips).
* **LOW #15** — hugepages JS bounds: typed values like `100000`
  used to silently pass to server; now caught at `n > 65536`.

## [0.5.77] - 2026-06-09

**Link status + audit batch 2 (HIGH operability gaps).** Operator
on srv06:

> also show the link status of interface on admin console, also
> check any further gaps and bugs in admin console

Two parallel audit fan-outs returned **27 findings** across the
admin HTML/JS and the /api/admin/* + /api/dpdk/* REST surface.
This release ships the operator-explicit ask plus the three
HIGH findings that were dropping payload on the floor.

Full suite: **2,076 passed, 1 skipped** (+9 new tests).

### Link status (operator ask)

`/api/dpdk/interfaces` items now include a `link` block:

```json
"link": {
  "carrier": true,
  "speed_mbps": 25000,
  "duplex": "full"
}
```

Sourced from sysfs `/sys/class/net/<iface>/{carrier,speed,duplex}`.
No ethtool subprocess (~50ms saved per call). Renders as a muted
second line under the State pill so the table column count is
unchanged:

| State |
|---|
| ✓ DPDK-ready (kernel mlx5_core)<br><sub>↑ 25 Gb/s full</sub> |
| ✗ Kernel (tg3)<br><sub>↓ link down</sub> |

### Audit HIGH #1 — reboot_needed banner

`/api/admin/health` has reported `reboot_needed` + `reboot_reasons[]`
since v0.5.69 (after Configure IOMMU, after kernel-module changes)
but the JS never read either field. Operator saw a green toast,
walked away thinking it took effect. New gold sticky banner:

```
⚠ Host reboot required: IOMMU enable applied, vfio-pci modules
  reloaded
```

### Audit HIGH #2 — install/upgrade-in-progress banner

`/api/admin/health` exposes `install_running`, `rdma_install_running`,
`upgrade_running`. JS only consumed the first → operators
clicking destructive actions mid-wheel-upgrade raced and lost.
New blue sticky banner:

```
⟳ Wheel upgrade in progress — destructive actions are blocked
  until it finishes.
```

### Audit endpoint #1 — install lock (HIGH TOCTOU)

`/api/admin/install_dpdk` had a check-then-set race:

```
POST A reads state → sees idle
POST B reads state → sees idle
POST A spawns Popen, writes state
POST B spawns Popen, OVERWRITES state ← orphans A's process
```

New `_ADMIN_INSTALL_LOCK` held around the **re-check + Popen +
state-set** window. If the slot is claimed during the window,
the late request gets a clean 409.

### Remaining audit findings (24 deferred to v0.5.78+)

Categorized for the next drive:

- **HIGH operability:** netgen-server service status card (#4),
  innerHTML driver/PCI validation (#3)
- **MEDIUM**: toast queue overlap (#5), connection-lost banner
  (#6), modal a11y (#7), NIC table MAC/MTU/NUMA (#8), disk-space
  visibility (#9), active-streams strip (#10), journalctl
  download (#11), bind confirm shows IPs/streams/route (#12),
  iommu reboot scheduling (#7-end), unbounded paginated lists
  (#9-end), GET endpoints leaking PCI topology unauthed (#5-end)
- **LOW:** accel empty-state visibility on AMD (#13), refresh
  button feedback (#14), hugepages JS bounds (#15), tighten
  PCI class 0x02 to {0200, 0207} (#8-end), `_NO_PMD_DRIVERS`
  hoist to module scope for iface preemptive flagging (#12-end)

## [0.5.76] - 2026-06-09

**DPDK Accelerators card — ioatdma out of the iface table.** Operator
on srv06:

> check admin console, there are intel PCI which are not associated
> with interface, what are these, it also shows state DPDK
> accelerator (kernel ioatdma)

### What they are

Intel I/OAT (Crystal Beach) DMA engines built into Xeon CPUs. srv06
has 8 per socket × 2 sockets = **16 ioatdma devices** at PCI
`0000:00:01.0-7` and `0000:80:01.0-7`. They're real DPDK accelerators
(DPDK's dmadev API uses them for fast memory copies) but they are
**not** network interfaces.

### Why they were appearing in the iface table

The `dpdk-devbind.py --status` parser tracked section state by
header detection (`Network devices using kernel driver`, `Other
Network devices`, …). When dpdk-devbind emitted `Other DMA devices
using kernel driver`, that header wasn't recognised so
`current_section` kept the previous network value — 16 ioatdma
rows leaked through as network devices.

### Fix

* **`_detect_pci_class(pci)`** — pure sysfs read of
  `/sys/bus/pci/devices/<bdf>/class`. ~20 µs per call.
* **`/api/dpdk/interfaces`** filters out any device whose PCI base
  class isn't `0x02` (Network controller). The section-state-leak
  symptom can't bite anymore.
* **`/api/dpdk/accelerators`** — new GET endpoint scanning sysfs
  directly for ioatdma / idxd / qat / ntb_hw_intel. Returns
  individual entries + per-label aggregate counts.
* **Admin console** adds a **DPDK Accelerators** card above the
  iface table with a count summary
  (`16 × Intel I/OAT DMA`) and a collapsible per-device table.
  Hidden when no accelerators are present.

### What srv06 will see after upgrade

```
┌─ DPDK Accelerators ──────────────────────────┐
│ 16 × Intel I/OAT DMA                         │
│ ▶ Show per-device list (16)                  │
└──────────────────────────────────────────────┘

┌─ Network Interfaces ─────────────────────────┐
│ ens3f0np0  …  NVIDIA Mellanox  …  no bind   │
│ ens10f0–3  …  Broadcom BCM5719  …  no PMD    │
│                                              │
│ (the 16 ioatdma rows no longer pollute       │
│  this table)                                 │
└──────────────────────────────────────────────┘
```

11 new regression tests. Full suite: **2,067 passed, 1 skipped**.

## [0.5.75] - 2026-06-09

**Refuse DPDK bind on tg3/e1000e/r8169 — no PMD, no hang.** Operator
on srv06:

> trying to bind dpdk on ens10f3 from admin console, screen seems
> to be hanging and does not enable DPDK.

### Root cause

`ens10f3` is a BCM5719 (kernel driver `tg3`). DPDK's bnxt PMD only
supports modern NetXtreme-E/Thor controllers — NOT the older
NetXtreme/tg3 family (BCM5717/5719/5720). Click flow pre-fix:

1. Operator clicks Bind. Tooltip warned but button stayed clickable
   (v0.5.47 only added the tooltip).
2. `bindInterface` POSTs `/api/dpdk/bind`. Server runs dpdk_bind.sh
   for ~30 s — vfio-pci claims the device at sysfs level.
3. Response returns, button re-enables, refresh shows status =
   DPDK-bound — but no DPDK app can use the card. Operator's screen
   "hangs" for 30s with no actionable result.

Worse: `bindInterface` had no try/catch around `fetch`, so a network
error during the bind (e.g. management iface disconnected) died
silently — the `.finally` re-enabled the button without surfacing
the error.

### Fix

**Server-side (`/api/dpdk/bind`):** new NO_PMD guard. Reads
`/sys/bus/pci/devices/<bdf>/driver` symlink, checks against
`{tg3, e1000, e1000e, e100, r8169, atlantic}`. Returns 409 with
`code="NO_PMD"`, `can_force=True`, and a tailored error message.
`force=true` overrides (operator may run an out-of-tree PMD).

**JS:** Bind button renders **disabled** with label "Bind (no
PMD)" for tg3-class drivers — not just tooltipped. Hint still
shows on hover.

**JS:** `bindInterface` fetch wrapped in try/catch. Network
errors surface a toast explaining the bind may have disconnected
the management NIC. Immediate "Binding <iface>…" toast on click
so the 5–30 s subprocess wait doesn't feel like a hang.

**JS:** 409 + `code=NO_PMD` surfaces a tailored confirm dialog
("…vfio-pci will claim it but DPDK apps won't recognise the
device. Force bind anyway?") — before the generic active-routes
check.

### What srv06 will see after upgrade

| Iface | Vendor | Bind button |
|---|---|---|
| ens3f0np0 | NVIDIA Mellanox (ConnectX-?) | "no bind needed" (bifurcated) |
| ens10f0–3 | Broadcom BCM5719 (tg3) | **Bind (no PMD)** disabled |

11 new regression tests. Full suite: **2,056 passed, 1 skipped**.

## [0.5.74] - 2026-06-09

**RDMA status in admin console + audit batch 1 (F1+F3+F6).** Operator
on srv06:

> also audit full rdma and make sure admin console should show
> the status of RDMA

Three independent audit fan-outs returned 32 findings across the
RDMA REST surface, support modules, and GUI client. This release
ships the operator-explicit ask (F6 — RDMA status in admin
console) plus the two security findings that mirror v0.5.68's
DPDK fortification.

Full suite: **2,045 passed, 1 skipped** (+12 new tests).

### F1 — auth gates on destructive RDMA endpoints

`@require_role("operator")` added to:
- `POST /api/rdma/perftest/start` (spawns long-running perftest
  subprocess; pre-fix any viewer token could DoS the box by
  spinning up the 64-job cap and holding QPs)
- `POST /api/rdma/perftest/stop` (kills a perftest job)
- `DELETE /api/rdma/handshakes/<id>` (mutates the registry)

Mirrors v0.5.68's DPDK gating exactly.

### F3 — strict bool on perftest flags

Pre-fix `{"bidirectional": "false"}` (string) silently enabled
`-b` on `ib_send_bw` — the truthy-string class that bit DPDK in
v0.5.68. Applied to `bidirectional`, `use_event`, `cpu_util`,
`report_gbits`, `forget_pair`.

### F6 — RDMA stack in /api/admin/health + admin console card

Operator-requested. `/api/admin/health` previously surfaced DPDK
state but **nothing** about RDMA. Now reports `out["rdma"]` with
`perftest_installed`, `tools`, `modules_loaded` (ib_uverbs,
rdma_cm, rdma_ucm, ib_umad, iw_cm), `hca_count`, `ports_active`,
`ports_total`. Two new degraded-state `issues` entries.

New **"RDMA Stack"** card in the admin HTML alongside DPDK
Runtime / Kernel Prereqs / Hugepages.

### Remaining audit findings (deferred to v0.5.75+)

29 findings stayed off this release. Categorized list ready
for the audit-release drive:

- **HIGH:** F2 device-name regex, F4 perftest watchdog +
  zombie reaper, F5 stderr surfacing, M4 perftest stdio
  buffering, M5 spawn/register race, G1 QThread parent
  ownership
- **MEDIUM:** F7-F11, M1-M3, M6-M10, G2-G10
- **LOW:** F12 ibv_devinfo memoize, sundry polish

## [0.5.73] - 2026-06-09

**Admin iface table surfaces NIC card model.** Operator-requested
on srv06:

> admin console should also show type of nic card used,
> CX5/CX7/CX6/CX8/Thor2/AMD Pollara/AMD Vulcano.. etc

Full suite: **2,033 passed, 1 skipped** (+7 new tests).

### What got added

* `_NIC_MODEL_DB` — curated map `(vendor_id, device_id)` →
  marketing name. Covers:
  * **NVIDIA/Mellanox** ConnectX-3, ConnectX-3 Pro, ConnectX-4,
    ConnectX-4 Lx, ConnectX-5, ConnectX-5 Ex, ConnectX-6,
    ConnectX-6 Dx, ConnectX-6 Lx, **ConnectX-7**, **ConnectX-8**,
    BlueField-2 DPU, BlueField-3 DPU
  * **Broadcom** Thor (BCM57508/57504/57502), **Thor 2**
    (BCM57608/57604), NetXtreme-E BCM57412/14/16/17, older tg3
    family (BCM5717/19/20)
  * **AMD Pensando** DSC (Naples), DSC2, Capri, **Pollara 400**
  * **Intel** I350, X710, XL710, XXV710 25G, X550, E810
    family (Columbiaville)
* `_detect_nic_model(pci)` — pure sysfs read of
  `/sys/bus/pci/devices/<bdf>/{vendor,device}`. No subprocess
  (called on every interfaces poll, ~200 µs per call).
* `card_model` field added to `/api/dpdk/interfaces` items.
* Admin JS folds the model as a muted second line under the
  vendor cell — keeps the 8-column layout.

### Vulcano note

AMD Vulcano (next-gen Pensando) device IDs aren't publicly
enumerated as of writing. The DB structure makes adding
entries trivial when IDs land.

### What srv06 will show after upgrade

| Interface | Vendor cell |
|---|---|
| ens3f0np0 | NVIDIA/Mellanox<br><sub>ConnectX-? (TBD on probe)</sub> |
| ens10f0 | Broadcom<br><sub>BCM5720 NetXtreme (tg3)</sub> |

## [0.5.72] - 2026-06-09

**Admin UI polish + worker-stall fix.** Audit M6 + M8 + M11 +
LOWs.

Full suite: **2,026 passed, 1 skipped** (+8 new tests).

* **M6** `_module_loaded` (inside `api_admin_health`) memoized
  for 10 s — pre-fix 4 subprocs × up to 5s each blocked the
  Flask worker every 30 s on contended boxes. Also: two-clause
  builtin check parity with `/api/dpdk/verify` (`"(builtin)" in
  stdout OR "filename:" not in stdout`).
* **M8** per-row Bind / Unbind handlers use `toastFailDetailed`
  instead of bare `'Bind failed: ' + d.message` — stderr no
  longer lost.
* **M11** `btn-refresh` wrapped in `withButtonBusy`.
* **LOW** `document.title` set to `Netgen admin — <hostname>` so
  multi-tab operators can distinguish servers; danger
  `Configure IOMMU` button gets ⚠ glyph + `aria-label`.

Final audit drive — all CRITICAL / HIGH / MEDIUM / LOW closed
across the admin console audit.

## [0.5.71] - 2026-06-09

**Operability batch — hugepages lock, rc=75 surface, install
mutex, kill switch, log pruning.** Audit M1+M2+M3+M4+M5.

Full suite: **2,018 passed, 1 skipped** (+7 new tests).

* **M1** `/api/dpdk/hugepages` non-blocking try-acquires
  `_DPDK_BIND_LOCK` → 409 on contention instead of racing the
  sysfs write. `finally:` releases.
* **M2** `/api/admin/install_dpdk/log` now reports
  `reboot_required: true` and `success: true` when the script
  exits 75 (v0.5.51's EX_TEMPFAIL "success, reboot needed"
  convention). Operator sees green "reboot required" instead
  of red "rc=75".
* **M3** install_dpdk and install_rdma 409 each other when the
  other is running (dpkg-lock contention otherwise wedges).
* **M4** `?force=1` (or `{"force": true}`) SIGTERMs a wedged
  install/upgrade and proceeds with the new spawn. Recovery
  without SSH.
* **M5** `/tmp/netgen_install_*.log` older than 7 days pruned on
  spawn. Best-effort.

7 new tests in `tests/test_v0571_operability.py`.

## [0.5.70] - 2026-06-09

**Input validation hardening on 3 admin endpoints.** Audit
findings H2 + H3 + M7.

Full suite: **2,011 passed, 1 skipped** (+10 new tests).

### H2: `/api/admin/bind_history` POST

* PCI BDF regex (same as v0.5.60)
* `name` ≤ 64 chars, `[A-Za-z0-9._:-]+`
* `kernel_driver` ≤ 64 chars, `[A-Za-z0-9_]+`
* Registry capped at 256 entries
* Read-modify-write held inside `_BIND_REGISTRY_LOCK` for the
  whole window (v0.5.58 fixed the helpers; the public POST
  handler did the cycle outside the lock and lost concurrent
  writes)

### H3: `/api/admin/interface_ip` address

* Parsed via `ipaddress.ip_interface(f"{address}/{prefix}")`
* CR, LF, and TAB added to the metachar denylist
  (`0.0.0.0\r\nGET /` used to slip through and inject a line
  into the request log)
* 45-char length cap (IPv6 textual max)

### M7: `/api/dpdk/hugepages` num_pages

* Upper bound 65536 — 32 GiB of 2MB pages, 64 TiB of 1GB pages,
  well above any real workload. Error message states the max.

### Tests

10 new in `tests/test_v0570_input_validation.py`.

## [0.5.69] - 2026-06-09

**`/api/admin/health` schema parity.** Audit H1.

Full suite: **2,001 passed, 1 skipped** (+8 new tests). Crossed
2,000 ✓.

The admin console polls this endpoint every 30s. Over the last
20 releases we added fields to `/api/dpdk/status` that never
made it to the consolidated health endpoint — so the admin chip
could (and did) show "healthy" while one of these subsystems
was wrong.

### New fields

```json
{
  "reboot_needed": false,
  "reboot_reasons": [],
  "hugepages": {
    "total": 1024,
    "free": 1024,
    "mounted": true,                  // v0.5.39 trap detection
    "mount_point": "/dev/hugepages",
    "per_size": {"2MB": 1024, "1GB": 0},          // v0.5.59
    "per_node": {"node0": {"2MB": 512, "1GB": 0}, // v0.5.54
                 "node1": {"2MB": 512, "1GB": 0}}
  },
  "dpdk": {
    "installed": true,
    "version": "23.11.0",
    "target_version": "23.11",        // v0.5.61 target
    "version_mismatch": false
  },
  "rdma_install_running": false,
  "upgrade_running": false
}
```

### New degraded-state checks

The `issues` list now flags:
* `host reboot required` (from the v0.5.51 marker)
* `hugepages allocated but hugetlbfs not mounted` (v0.5.39 trap)
* `DPDK X != target 23.11 (tx_worker may need rebuild)` (v0.5.61)

### Tests

8 new in `tests/test_v0569_admin_health_schema_parity.py`.

## [0.5.68] - 2026-06-09

**Admin security fortification.** Audit findings C1 + C2 + C3 + C4.

Full suite: **1,993 passed, 1 skipped** (+10 new tests).

### C1 — `@require_role("admin")` on 9 destructive endpoints

Pre-fix only `/api/admin/upgrade_wheel` was gated. Added to:
* `/api/dpdk/bind`, `/api/dpdk/unbind`
* `/api/dpdk/hugepages`, `/api/dpdk/iommu`, `/api/dpdk/load_modules`
* `/api/admin/install_dpdk`, `/api/admin/install_rdma`
* `/api/admin/interface_ip`, `/api/admin/bind_history`

When no auth env is set the role check no-ops, so existing
lab deployments don't break. When auth IS configured, a viewer-
token holder can no longer reboot the host or brick a NIC.

### C2 — `/api/dpdk/iommu` reboot now requires literal True + confirm token

Pre-fix `reboot = data.get("reboot", False)` then `if reboot
and enable:` triggered `sh -c "sleep 10 && reboot"`. Any
truthy value worked — including JSON `reboot: "false"` (a
truthy string!). Now:
1. `_strict_true(value)` returns `value is True` — only literal
   JSON `true` passes
2. Sibling `confirm: "REBOOT"` field required, else 400

### C3 — `/api/dpdk/bind` `force` flag also strict

Same shape as C2. `force: "no"` (truthy string) used to bypass
the v0.2.76 bind-safety guard. Now `_strict_true(force)` rejects
all non-`True` values.

### C4 — MAX_CONTENT_LENGTH + wheel hardening

* `app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024` —
  body-size cap. Pre-fix any client could upload 50 GB and
  ENOSPC `/tmp` (tmpfs).
* `werkzeug.utils.secure_filename` on the uploaded wheel name
  before joining with the save directory (defense in depth
  over the existing `_allowed_wheel_name` regex check).
* Wheel CONTENT validation:
  - Open as `zipfile`
  - Find `*.dist-info/METADATA`
  - Parse `Name:` line; reject if not `ostg-trafficgen`
  - On validation failure: unlink the saved file and 400

Pre-fix an admin-token holder could upload e.g. `pwn-1.0-py3-
none-any.whl` and pip would install it into the venv, replacing
the running entry point.

### Sandbox-EPERM hexalogy now closed at this layer

(For audit traceability — v0.5.68 doesn't change the EPERM
class, just notes that the security side is also locked down.)

### Tests

10 new in `tests/test_v0568_admin_security_fortification.py`:
* 9 destructive endpoints carry `@require_role("admin")`
* `MAX_CONTENT_LENGTH` set and within sane range
* `_strict_true(v)` helper compares with `is True`
* iommu reboot uses helper AND requires confirm token
* bind force uses helper
* Wheel upload uses `secure_filename`
* Wheel content validated as zipfile with `ostg-trafficgen` METADATA
* Failed-validation uploads unlinked
* Version pinned at ≥ 0.5.68

## [0.5.67] - 2026-06-09

**`/api/admin/health` now finds tx_worker at `/usr/local/bin/`.**
Operator-reported on srv06 (v0.5.59) admin console:

> DPDK Runtime
> tx_worker binary    Not built

But every other endpoint disagreed:
* `/api/dpdk/verify` → "tx_worker found: /usr/local/bin/tx_worker"
* `/api/dpdk/status` → `tx_worker_exists: true`, `tx_worker_built: "2026-06-08 04:47"`

Full suite: **1,983 passed, 1 skipped** (+4 new tests).

### Cause

Two different code paths probing different locations. The admin
health endpoint only checked:
```
/opt/netgen/resources/dpdk/tx_worker/build/tx_worker
/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
```

But `install_dpdk.sh`'s Step 6 installs the built binary to
`/usr/local/bin/tx_worker` — same path the rest of the server
(e.g. `start_traffic`) actually invokes. The admin endpoint just
never had this path in its list.

### Fix

```python
candidates = [
    "/usr/local/bin/tx_worker",                                       # install target (preferred)
    "/opt/netgen/resources/dpdk/tx_worker/build/tx_worker",           # wheel-bundled build dir
    "/opt/netgen-server/resources/dpdk/tx_worker/build/tx_worker",    # tarball-internal build dir
    "/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker",             # pre-v0.5 compat symlink
]
```

`/usr/local/bin/` first because that's what `start_traffic`
actually invokes — if the admin card says ✓, runtime works. The
build-dir paths stay as fallbacks for early-install hosts where
Step 6 hasn't run yet.

## [0.5.66] - 2026-06-09

**Hugepages allocation: wrap `mount` in `systemd-run` to escape
the CAP_SYS_ADMIN restriction.** Operator-reported on srv06
(v0.5.59):

```
HTTP 500
Failed to mount hugetlbfs at /mnt/huge: Command ['mount', '-t',
'hugetlbfs', 'nodev', '/mnt/huge'] returned non-zero exit
status 32. Sysfs allocation rolled back.
```

Full suite: **1,979 passed, 1 skipped** (+7 new tests).

### Why

`mount(2)` requires `CAP_SYS_ADMIN`. The v0.5.56 caps drop-in
adds that — but only to **newly-started** processes. The
v0.5.55 → v0.5.59 upgrade restarted netgen-server BEFORE the
drop-in was written (same v0.5.49 catch-22). So the running
process still has the pre-v0.5.56 cap set, and `mount` returns
EPERM → exit 32 → endpoint rolls back the sysfs allocation.

### Fix

```python
systemd_run = _systemd_run_available()
if systemd_run:
    _mount_cmd = [
        systemd_run,
        "--wait", "--pipe", "--collect",
        f"--unit=netgen-mount-hugetlbfs-{int(time.time())}.service",
        "--description=netgen mount hugetlbfs (cgroup-escape)",
        "--",
        "mount", "-t", "hugetlbfs", "nodev", mount_point,
    ]
else:
    _mount_cmd = ["mount", "-t", "hugetlbfs", "nodev", mount_point]
subprocess.run(_mount_cmd, check=True, timeout=15)
```

systemd-run spawns the mount in a fresh transient unit with
vanilla caps — kernel grants CAP_SYS_ADMIN unconditionally. The
hugepages allocation works regardless of the netgen-server
process's own cap set. Same pattern as v0.5.33 (apt cache
chmod) and v0.5.44 (modprobe init_module).

### Sandbox-EPERM pentalogy

| Release | Operation | Cause | Workaround |
|---|---|---|---|
| v0.5.31 | apt setgroups | RestrictSUIDSGID | -o APT::Sandbox::User=root |
| v0.5.33 | apt cache chmod | ProtectSystem | systemd-run wrap |
| v0.5.44 | modprobe init_module | ProtectKernelModules | systemd-run wrap |
| v0.5.50 | sudo setresuid | CAP_SETUID missing | skip sudo when root |
| **v0.5.66** | **mount(hugetlbfs)** | **CAP_SYS_ADMIN missing** | **systemd-run wrap** |

The v0.5.56 cap drop-in is still the cleaner long-term answer
— once netgen-server is restarted to pick up the new caps, the
in-process mount would also work. v0.5.66 makes allocation
work BEFORE that restart.

### Tests

7 new in `tests/test_v0566_mount_systemd_run.py`:
* Mount call wrapped in systemd-run when available
* Wrap uses --wait + --pipe + --collect
* Falls back to bare mount when systemd-run unavailable
* subprocess.run uses the constructed _mount_cmd variable
* Transient unit name has unique timestamp suffix
* subprocess timeout bumped to ≥ 15s for systemd-run overhead
* Version pinned at ≥ 0.5.66

## [0.5.65] - 2026-06-09

**LOW polish batch — operstate casing parity, visibility-aware
polling, iface table overflow, accessible pill glyphs.**

Full suite: **1,972 passed, 1 skipped** (+7 new tests).

* `/api/admin/interface_ips` returns lowercase operstate to
  match `/api/interfaces` (v0.5.43 standardised on lowercase;
  this endpoint was the odd one out).
* `setInterval(refreshHealth)` guards with
  `document.visibilityState === 'visible'`. Resumes immediately
  on `visibilitychange` instead of waiting up to 30 s.
* Iface table wrapped in `<div style="overflow-x:auto">` so
  narrow viewports keep the action column accessible.
* Pills get a glyph prefix (`✓` / `✗` / `!`) plus `aria-label`
  for color-blind operators + screen readers.

## [0.5.64] - 2026-06-09

**Admin UI polish — tri-state hugepages pill, detailed error
toast, button-busy guard.** Audit M13 + M14 + M15.

Full suite: **1,965 passed, 1 skipped** (+9 new tests).

* **M13** hugepages color now tri-state: red `total=0`, orange
  `free=0` (exhausted — typical leaked-DPDK-process sign),
  ink otherwise. Pre-fix the exhausted state showed as normal
  text and the operator missed it during RFC 2544 failures.
* **M14** `toastFailDetailed(d, status)` helper falls through
  `d.message → d.error → d.output → d.stderr → HTTP <code>` so
  the actual error reason (e.g., dpdk_bind.sh stderr) reaches
  the operator instead of "unknown".
* **M15** `withButtonBusy(btnId, fn)` disables the button on
  entry, re-enables in `finally`. Applied to `btn-load-modules`,
  `btn-config-iommu`, `btn-config-hp`. Triple-clicks no longer
  trigger three concurrent installs.

## [0.5.63] - 2026-06-09

**netgen-dpdk-rebind.service — cleaner dependency edge +
survives NIC hot-remove.** Audit findings M11 + M12.

Full suite: **1,956 passed, 1 skipped** (+6 new tests).

### M11: unit hygiene

* Dropped `Wants=systemd-modules-load.service` — redundant
  (target is already `WantedBy=sysinit.target`).
* Added `ConditionPathExists=/etc/netgen/dpdk-interfaces.json`
  so hosts that never ran DPDK setup skip the unit cleanly
  instead of logging "nothing to do" every boot.

### M12: hot-remove survival

Pre-fix: a single failed bind set `rc=1` → unit went to
"failed" → systemctl `After=netgen-dpdk-rebind.service`
consumers blocked. After a NIC hot-remove or BIOS PCI
renumber the operator woke up to a wedged boot.

Fix in the helper script:
1. Pre-check `/sys/bus/pci/devices/<bdf>`. If missing → log
   SKIP, add to `missing` list (don't even try dpdk-devbind).
2. Track per-entry success / failure / missing.
3. **Prune** missing entries from
   `/etc/netgen/dpdk-interfaces.json` so subsequent boots don't
   keep tripping over them. Atomic write (tmp + os.replace).
4. Return 0 if ANY bind succeeded OR we pruned anything —
   downstream services stay unblocked.

## [0.5.62] - 2026-06-09

**install_rdma.sh refreshes modules-load file when module set
has changed.** Audit M9.

Full suite: **1,950 passed, 1 skipped** (+3 new tests).

Pre-fix: `if [[ ! -f $modules_load_file ]]; then write; else
skip`. v0.5.28 added `rdma_ucm` + `iw_cm` to the `rdma_modules`
array. Hosts upgraded from v0.5.27 still had only the old three
modules in their boot-time list. Step 2's modprobe loop loaded
everything for the current session, but on the next reboot
`rdma_ucm`-needing tools (`ib_send_bw`, `rping`) failed with
EBADF on `/dev/infiniband/rdma_cm` until the operator noticed
and manually modprobed.

Fix: compare desired vs current content; rewrite on mismatch.
File is ~80 bytes and we own it — no reason to skip.

## [0.5.61] - 2026-06-09

**install_dpdk.sh polish — kernel-headers fallback, multi-mount
disk-space check, version-mismatch warning.** Audit M6 + M7 + M8.

Full suite: **1,947 passed, 1 skipped** (+4 new tests).

* **M6:** `apt-cache show "linux-headers-$(uname -r)"` first;
  fall back to `linux-headers-generic` when the precise version
  isn't in the apt repo (out-of-band kernels, HWE roll-forward,
  custom kernels, snapshot rollback).
* **M7:** disk-space check now walks `/`, `/usr/local`, and the
  DPDK build dir — separate `/opt` or `/usr/local` mounts no
  longer bypass it.
* **M8:** `check_dpdk_installed` warns when installed version
  differs from 23.11 target, calling out that **tx_worker
  linked against the old ABI will break until rebuilt**.

## [0.5.60] - 2026-06-09

**IOMMU regex anchoring + cpu_vendor allowlist + PCI BDF
validation.** Audit findings M3 + M4.

Full suite: **1,943 passed, 1 skipped** (+6 new tests).

### M3: `/api/dpdk/iommu` substring → word-boundary regex

Pre-fix `iommu_param not in current_cmdline` substring check.
On a cmdline already containing `intel_iommu=on,igfx_off`
(comma-flags form) the check matched but the boundary was
wrong → duplicates accumulated on repeat calls.

Fix: `re.search(rf'\b{re.escape(iommu_param)}\b', current_cmdline)`.

Plus `cpu_vendor` now allowlisted — only `'intel'` / `'amd'`.
Pre-fix any other string (typo like `'intl'`) fell through to
the Intel branch and wrote Intel IOMMU params on AMD boxes.
The AMD kernel quietly ignores them → IOMMU stays off →
vfio-pci fails downstream.

### M4: PCI BDF strict regex

Pre-fix `/api/dpdk/bind` + `unbind` only checked `":" in pci`.
`0000:01:00.0; rm -rf /` passed. subprocess.run uses a list (not
shell=True) so no shell-injection, but the value poisoned
dpdk_bind.sh's word-splitting → confusing downstream errors.

Fix: `re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$", pci)`.
Canonical lowercase BDF from sysfs is the only safe form.

## [0.5.59] - 2026-06-09

**1GB hugepage support + num_pages input validation.** Audit
finding M2.

Full suite: **1,937 passed, 1 skipped** (+6 new tests).

1GB hugepages are standard for ≥100 Gbps DPDK on AMD EPYC /
Intel Sapphire Rapids. Pre-fix the endpoint flat-rejected
`page_size: "1GB"` — operators had to set them via GRUB cmdline
only (boot-time, no runtime resize). The sysfs path mirrors the
2MB layout: `/sys/.../hugepages-1048576kB/nr_hugepages`.

Plus: `num_pages` now validated as non-negative int up front
instead of letting the kernel silently clamp negatives to 0.

```python
_HUGEPAGE_LEAVES = {
    "2MB": "hugepages-2048kB/nr_hugepages",
    "1GB": "hugepages-1048576kB/nr_hugepages",
}
```

6 new tests pin the dict mapping, the error response with
supported list, and the validation.

## [0.5.58] - 2026-06-09

**Registry hygiene — atomic write + shared lock.** Audit findings
M1 + M5.

Full suite: **1,931 passed, 1 skipped** (+7 new tests).

### M1: `/api/admin/bind_history` non-atomic write

```python
def _save_bind_history(history):
    with open(_ADMIN_BIND_HISTORY_PATH, "w") as f:  # ⚠ truncates
        json.dump(history, f, indent=2)
```

Two concurrent POSTs (operator UI + scripted client) raced:
- Thread A: `open("w")` truncates the file
- Thread B: `open("r")` reads → 0-byte file
- Thread A: `json.dump` completes

Thread B got `{}` instead of the real history → next rebind
boot lost the original-driver memory.

Fix: write to `.tmp` + `os.replace` (atomic rename).

### M5: `_dpdk_persist_bind` / `_dpdk_unpersist_bind` no lock

Already had atomic write (.tmp + os.replace) but no
serialisation across calls. Two concurrent `/api/dpdk/bind`
calls read-modify-write the same file — one bind's entry got
lost.

Fix: shared `threading.Lock()` (`_BIND_REGISTRY_LOCK`) held by
all 4 sites (`_load_bind_history`, `_save_bind_history`,
`_dpdk_persist_bind`, `_dpdk_unpersist_bind`). The lock cost is
negligible in the common case; the race window is closed
completely.

### Tests

7 new in `tests/test_v0558_registry_lock_and_atomic.py`:
* Shared lock declared
* `_save_bind_history` atomic write (.tmp + os.replace)
* All 4 functions hold the lock
* Version pinned at ≥ 0.5.58

## [0.5.57] - 2026-06-09

**Admin console JS — in-flight guards + bind-history XSS escape.**
Audit findings H9 + H10.

Full suite: **1,924 passed, 1 skipped** (+8 new tests).

### H9: race on `refreshHealth` + `refreshInterfaces`

Every bind / unbind / load-modules / configure-hugepages click
fires both refreshes in `.finally`. Each refresh makes up to 4
parallel `fetch()` calls. The 30 s `setInterval(refreshHealth,
30000)` collides too.

Multi-NIC operator clicking 3 actions in quick succession:
12+ parallel fetches racing. Slower response's
`innerHTML`-overwrite wins → table flickers to stale state for
a few seconds.

Fix: in-flight boolean + rerun flag.
```javascript
let _healthInFlight = false, _healthRerun = false;
async function refreshHealth() {
  if (_healthInFlight) { _healthRerun = true; return; }
  _healthInFlight = true;
  try { /* fetches */ }
  finally {
    _healthInFlight = false;
    if (_healthRerun) { _healthRerun = false; refreshHealth(); }
  }
}
```
Same pattern for `refreshInterfaces`.

### H10: `history[pci].name` XSS escape

```javascript
// Before
name = `${history[pci].name} <span ...>(DPDK)</span>`;
// After
name = `${escapeHtml(history[pci].name)} <span ...>(DPDK)</span>`;
```

`history[pci].name` comes from operator-POSTed
`/api/admin/bind_history`. Admin-token-gated so risk is bounded
to privilege-escalation-from-already-admin, but defense-in-depth.

### Tests

8 new in `tests/test_v0557_admin_js_race_and_xss.py`:
* `_healthInFlight` / `_ifacesInFlight` flags declared
* refreshHealth early-returns on in-flight + sets rerun
* refreshHealth clears the flag in `finally`
* finally re-invokes refreshHealth if rerun was set
* Same pattern for refreshInterfaces
* `history[pci].name` wrapped in `escapeHtml`
* No remaining raw `${history[pci].name}` interpolation
* Version pinned at ≥ 0.5.57

## [0.5.56] - 2026-06-09

**netgen-server.service now holds the capabilities DPDK
operations need.** Audit finding H8 — the cleaner fix that
obsoletes the workarounds piled up in v0.5.31/33/44/50.

Full suite: **1,916 passed, 1 skipped** (+9 new tests).

### The sandbox-EPERM tetralogy in one fix

| Workaround | Was for | Obsoleted by |
|---|---|---|
| v0.5.31 `-o APT::Sandbox::User=root` | apt setgroups() EPERM | CAP_SETUID + CAP_SETGID held |
| v0.5.33 `systemd-run` for apt cache chmod | ProtectSystem-ish EPERM | CAP_DAC_OVERRIDE held |
| v0.5.44 `systemd-run` for modprobe | ProtectKernelModules-equivalent EPERM | CAP_SYS_MODULE held |
| v0.5.50 skip-sudo-when-root | sudo's own setresuid() EPERM | CAP_SETUID held |

The workarounds **stay in the code** — they're correct for
defense-in-depth and they cover sites where this self-heal
hasn't run yet. But going forward, new DPDK operations don't
need new gymnastics.

### What gets deployed

For **fresh tarball installs**, `scripts/tarball/netgen-install`
now writes the expanded set directly in the main unit:

```ini
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN CAP_SYS_ADMIN CAP_SYS_MODULE CAP_SYS_BOOT CAP_SETUID CAP_SETGID CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN CAP_SYS_ADMIN CAP_SYS_MODULE CAP_SYS_BOOT CAP_SETUID CAP_SETGID CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
```

For **existing installs** (srv06 included), the v0.5.56 server
on startup writes a drop-in:

```
/etc/systemd/system/netgen-server.service.d/netgen-caps.conf
```

That overlays the main unit without modifying it — so tarball
reinstalls don't conflict, and the operator can `rm` the
drop-in to revert.

### Catch-22 same as v0.5.49

The v0.5.55 → v0.5.56 upgrade restart uses the OLD caps. The
new caps apply on the NEXT restart after the self-heal writes
the drop-in.

To activate immediately on srv06:
```bash
ssh srv06 'systemctl restart netgen-server'
```

To verify the drop-in landed:
```bash
ssh srv06 'systemd-analyze cat-config netgen-server.service | grep -i Capability'
```

### Tests

9 new in `tests/test_v0556_systemd_caps_override.py`:
* Tarball installer writes expanded caps (Ambient + Bounding)
* Self-heal helper defined
* Uses drop-in path (not main unit edit)
* Override content lists all required caps
* SHA-compare skip when in sync
* Runs `systemctl daemon-reload` after write
* Helper called at startup
* Version pinned at ≥ 0.5.56

## [0.5.55] - 2026-06-09

**Install scripts: real apt-update exit code + apt failure log.**
Audit findings H6 + H7.

Full suite: **1,907 passed, 1 skipped** (+6 new tests).

### H6: `apt-get update` success detection

Pre-fix:
```bash
apt-get update -y ... 2>&1 | grep -q "Reading package lists"
```

False-positives when apt prints that line then fails for an
unrelated reason (DNS, repo signature, Hash mismatch) — we'd
mark `APT_UPDATE_SUCCESS=1` and proceed against a broken cache.
False-negatives when newer apt versions omit the literal string
on fully-cached refreshes.

Fix: pipe through `tail | sed` for log formatting; with `set -o
pipefail` (enabled in the script header), apt's real exit code
propagates through the pipeline. `if apt-get update ...; then`
checks the actual rc.

### H7: install_rdma.sh apt failure log

Pre-fix:
```bash
if ! eval "$core_apt_cmd" 2>&1; then
    log_error "Core RDMA package install failed."
    exit 2
fi
```

Failure output went to terminal only. The wizard's log capture
showed `exit 2` with nothing else — operator had to SSH in and
re-run apt to see the actual error. Directly contradicted the
v0.5.30 lesson learned in install_dpdk.sh.

Fix: `tee` output to `/tmp/rdma_deps_install.log` (matches
install_dpdk.sh's `/tmp/dpdk_deps_install.log` convention).
On failure: tail the log into the error output so the wizard
captures it inline.

### Tests

6 new in `tests/test_v0555_install_apt_detect_and_log.py`:
* `grep -q "Reading package lists"` removed from retry loop
* Pipefail-propagated exit code check pattern present
* install_rdma.sh apt install tees to a log file
* Failure path tails the log into error output
* Log path follows `/tmp/<thing>_deps_install.log` convention
* Version pinned at ≥ 0.5.55

## [0.5.54] - 2026-06-09

**NUMA-aware hugepage allocation.** Audit finding H5.

Full suite: **1,901 passed, 1 skipped** (+8 new tests).

### The bug

`/api/dpdk/hugepages` always wrote to the global:
```
/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

On dual-socket boxes (typical lab DPDK hosts), the kernel
opportunistically allocates pages on whichever NUMA node has
contiguous memory available — often NOT the NIC's NUMA node.
DPDK apps then fail with:
```
EAL: Cannot allocate memory on socket 1
EAL: Failed to initialize memory pool
```
even though `/proc/meminfo` shows `HugePages_Total: 2048`.
Allocation "succeeded" → 0 usable pages on the NIC's socket.

### Fix

1. **Read `/sys/devices/system/node/online`** to detect NUMA
   topology. Handles both `0-1` range and `0,2,3` list formats.
2. **Multi-node hosts: split evenly** using `divmod`. Any
   remainder lands on node 0 (most common DPDK NIC home).
3. **Write per-node sysfs paths:**
   ```
   /sys/devices/system/node/node0/hugepages/hugepages-2048kB/nr_hugepages
   /sys/devices/system/node/node1/hugepages/hugepages-2048kB/nr_hugepages
   ```
4. **Read back** after each write — the kernel can short-
   allocate under fragmentation, so response reflects actual
   allocation, not just the request.
5. **Single-node fallback** keeps the global-path behaviour for
   UMA hosts and containers without per-node sysfs.

### Response gains two fields

```json
{
  "success": true,
  "requested": 2048,
  "actual_allocated": 2048,
  "numa_split": {"node0": 1024, "node1": 1024},
  "numa_nodes": [0, 1]
}
```

The admin chip can now show the distribution — operator
immediately sees whether allocation landed on the right
socket.

### Tests

8 new in `tests/test_v0554_numa_hugepages.py`:
* Reads `/sys/devices/system/node/online`
* Parses both `-`-range and `,`-list forms
* Writes per-node paths on multi-node hosts
* `divmod` for even distribution
* Reads back to detect short-alloc
* Falls back to global path on single-node
* Response includes `numa_split` + `numa_nodes`
* Version pinned at ≥ 0.5.54

## [0.5.53] - 2026-06-09

**Unbind restores the original kernel driver from the persistent
registry, even after reboot.** Audit finding H3.

Full suite: **1,893 passed, 1 skipped** (+8 new tests).

### The bug

The original-driver hint lived only in
`/tmp/netgen_admin_bind_history.json`, which dies on reboot.
Post-reboot unbind:

1. Read empty `kernel_driver` from request body
2. /tmp history was wiped → fall through
3. Call `dpdk_bind.sh unbind <PCI>` without `--kernel-driver`
4. Script falls back to vendor-ID heuristic → picks `ice` for
   vendor 0x8086 (Intel)

`ice` is wrong for:
* X710 / X722 → should be `i40e`
* X550 / X540 → should be `ixgbe`
* 82576 / 82574 → should be `igb` / `e1000e`

Result: unbind fails OR restores the wrong driver → NIC stays
driverless until the operator SSHs in and runs `modprobe i40e`
manually.

### Fix

1. **Bind path captures original driver** from
   `_load_bind_history()` (admin UI POSTs the current driver
   right before bind) and passes it through to
   `_dpdk_persist_bind(..., original_driver=...)`.

2. **Registry stores it.** `/etc/netgen/dpdk-interfaces.json`
   entries now have an `original_driver` field. Repeat-binds
   for the same PCI preserve the old field if the new call
   doesn't supply one.

3. **Unbind path reads it.** When request body has no
   `kernel_driver`, look up the registry's `original_driver`
   first. Fall back to /tmp history (in-session legacy binds),
   then to the dpdk_bind.sh heuristic.

### Tests

8 new in `tests/test_v0553_unbind_restores_original_driver.py`:
* `_dpdk_persist_bind` accepts `original_driver` kwarg
* Writes it to the registry entry
* Preserves it on repeat-bind when not re-supplied
* Bind handler captures from history before persist
* Unbind handler reads from registry
* Unbind falls back to /tmp history when registry empty
* Logs the restoration so operator can trace it
* Version pinned at ≥ 0.5.53

## [0.5.52] - 2026-06-09

**Anchored regex in /api/dpdk/verify + fstab AND-not-OR
idempotency.** Closes audit findings H1 + H2.

Full suite: **1,885 passed, 1 skipped** (+7 new tests).

### H1: `/api/dpdk/verify` module-detection regex anchoring

Pre-fix code:
```python
if "vfio" in output or "uio" in output:
    modules_loaded = True
```

Same bug class as v0.5.42 (fixed in `/api/dpdk/load_modules`) but
in a sibling endpoint. On srv06's kernel 6.8 with the vfio-pci
split, `vfio_pci_core` and `pds_vfio_pci` are loaded but bare
`vfio` and `vfio_pci` aren't — substring match returned True
anyway. Diagnostics would then claim `kernel_modules: true` and
skip the actual load step, leaving the operator stuck.

Fix: explicit per-module list with anchored regex:
```python
for _mod in ("vfio", "vfio_pci", "uio_pci_generic"):
    if re.search(rf'^{_mod}\s', result.stdout, re.MULTILINE):
        ...
```
Plus the message-building branch now reports the precise
detected list, not the same broken substring re-check.

### H2: fstab idempotency

Pre-fix:
```python
if mount_point not in existing or "hugetlbfs" not in existing:
    append
```

The OR was wrong. On systems where systemd's
`dev-hugepages.mount` already wrote `none /dev/hugepages
hugetlbfs ...` to fstab, the first clause was True (our
`/mnt/huge` isn't present) AND the second clause was False
(hugetlbfs IS present). The OR returned True → we appended a
duplicate entry on every `/api/dpdk/hugepages` call.

Fix: walk lines, skip comment lines, match a single non-comment
line that contains BOTH the mount point AND hugetlbfs. Also fix
trailing-newline glue when the existing fstab doesn't end with a
newline.

### Tests

7 new in `tests/test_v0552_verify_anchored_fstab_and.py`:
* Substring `"vfio" in ...` removed from verify executable code
  (commented documentation of the old bug is allowed)
* Anchored regex `re.search(rf'^{mod}\s', ...)` used
* Message builder uses the per-module detection list
* fstab no longer uses the top-level OR pattern
* Per-line AND check (mount_point + hugetlbfs in same line)
* Comment lines (`#`) skipped during the walk
* Trailing-newline check before our header
* Version pinned at ≥ 0.5.52

## [0.5.51] - 2026-06-09

**install_dpdk.sh now configures GRUB/IOMMU and signals reboot.**
Closes audit findings C3 (CRITICAL: no IOMMU setup → vfio-pci
binding succeeds but DPDK fails 1 second later with "No IOMMU
support") and C4 (CRITICAL: no reboot-needed signal → sysctl
persistence file written but running kernel keeps old value
silently).

Full suite: **1,878 passed, 1 skipped** (+12 new tests).

### New `step_configure_iommu` (between hugepages and NIC bind)

* Detects CPU vendor from `/proc/cpuinfo`
  (`GenuineIntel` / `AuthenticAMD`)
* Idempotent: if `/proc/cmdline` already has the params, skip
* Backs up `/etc/default/grub` with timestamp
  (`.netgen-bak.<epoch>`)
* Appends `intel_iommu=on iommu=pt` or `amd_iommu=on iommu=pt`
  to `GRUB_CMDLINE_LINUX_DEFAULT` (preferred) or
  `GRUB_CMDLINE_LINUX`
* Runs `update-grub` (Debian/Ubuntu) or `grub2-mkconfig` (RHEL)
* On failure: restores backup so the box stays bootable
* On success: marks reboot required + exits non-zero (75)

### `netgen_mark_reboot_required(reason)` helper

Tracks reboot-needed state across all install steps:

* `REBOOT_REQUIRED=1`
* Writes `/run/netgen-reboot-required` (or
  `/var/run/netgen-reboot-required` fallback for older systems)
* `step_summary` surfaces a yellow banner + lists every reason
* Exit code 75 = `EX_TEMPFAIL` — admin endpoint can distinguish
  "success but reboot needed" from plain success

### `step_configure_hugepages` now applies `sysctl --system`

Pre-fix the operator could set hugepages to 2048, write the
sysctl file, see "Hugepages persisted", and then notice the
running kernel still shows 1024. The fix runs `sysctl --system`
after writing so the new value is live (or marks reboot
required if the reload itself fails).

### `/api/dpdk/status` exposes reboot state

New fields:
* `reboot_needed: bool`
* `reboot_reasons: [str]` — from the marker file

So the admin chip can warn the operator even after the install
log has scrolled off.

### Why srv06 was working anyway

The operator's srv06 already had IOMMU active before install_dpdk
ever ran (manual setup at machine prep time). New hosts won't be
so lucky — that's exactly the C3 trap. v0.5.51 makes the install
self-sufficient.

### Tests

12 new in `tests/test_v0551_iommu_and_reboot_signal.py`:
* `step_configure_iommu()` defined
* Wired between hugepages and NIC binding in main()
* Detects Intel AND AMD vendors
* Idempotency check against `/proc/cmdline`
* Backs up `/etc/default/grub` before edit
* Supports both `update-grub` and `grub2-mkconfig`
* `netgen_mark_reboot_required()` helper exists
* Writes `/run/netgen-reboot-required` + `/var/run/...` fallback
* `step_summary` exits 75 when reboot required
* `step_configure_hugepages` calls `sysctl --system`
* `/api/dpdk/status` exposes `reboot_needed` + `reboot_reasons`
* Version pinned at ≥ 0.5.51

## [0.5.50] - 2026-06-09

**Don't call `sudo` from an already-root process.** Operator on
srv06 tried to bind `ens10f1` from the admin console (Jun 9
2026):

```
HTTP 500
sudo: PERM_SUDOERS: setresuid(-1, 1, -1): Operation not permitted
sudo: unable to open /etc/sudoers: Operation not permitted
sudo: error initializing audit plugin sudoers_audit
```

Full suite: **1,866 passed, 1 skipped** (+8 new tests).

### Root cause

`netgen-server.service` runs as root but its
`CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN` caps what the
process can hold — anything NOT in the list is permanently
dropped, even though the process is UID 0. So:

* `CAP_SETUID` dropped → `sudo`'s `setresuid(-1, 1, -1)` to
  switch to the sudoers-parser UID returns EPERM
* `CAP_DAC_OVERRIDE` dropped → can't open `/etc/sudoers` (mode
  0440 root:root)

The sudo binary fails BEFORE it ever runs the wrapped
`dpdk_bind.sh`. The bind never starts. Same class as v0.5.31
(apt setgroups EPERM) and v0.5.44 (modprobe init_module EPERM),
but hitting sudo's privilege-drop instead of a privileged
operation.

### Fix — `_maybe_sudo()` helper

```python
def _maybe_sudo(cmd):
    if os.geteuid() == 0:
        return list(cmd)        # we're root — no sudo needed
    return ["sudo"] + list(cmd)  # non-root install — sudo as before
```

Applied to **all 6** `["sudo", ...]` literals in DPDK paths:

* 2× `/api/dpdk/interfaces` status reads
* 1× `/api/dpdk/status` dpdk-devbind read
* 1× `/api/dpdk/bind`
* 1× `/api/dpdk/unbind`
* 1× `/api/dpdk/load_modules` sudo-fallback

On srv06 (where the unit runs as root) sudo is now bypassed
entirely → no EPERM. On non-root installs (rare) sudo still
runs as before.

### Why not just add caps to the unit?

The audit (H8) recommends adding `CAP_SYS_ADMIN
CAP_SYS_MODULE CAP_SYS_BOOT CAP_SETUID CAP_DAC_OVERRIDE` to
`netgen-server.service` so in-process syscalls (mount, modprobe,
direct sysfs writes, reboot) work without the
systemd-run/subprocess workarounds we've been piling up since
v0.5.31. That's a follow-up — it requires editing the installed
unit and reloading, which means a separate ship + tarball
re-install. This v0.5.50 fix sidesteps the immediate EPERM
without that surgery.

### Tests

8 new in `tests/test_v0550_dpdk_no_sudo_when_root.py`:
* `_maybe_sudo()` helper defined
* Returns cmd unchanged when `geteuid() == 0`
* Still prepends `sudo` when non-root
* No remaining `["sudo", ...]` literals in subprocess calls
* `/api/dpdk/bind` uses helper
* `/api/dpdk/unbind` uses helper
* `load_modules` sudo-fallback uses helper
* Version pinned at ≥ 0.5.50

## [0.5.49] - 2026-06-09

**Self-heal `/opt/netgen-server/bin/netgen-upgrade` from the
wheel-bundled copy.** Operator-reported on srv06 after upgrading
v0.5.47 → v0.5.48 via the admin UI:

> seems upgrade still doing uninstall and install.
> [INFO] $ pip install --force-reinstall --no-cache-dir <wheel>
> Attempting uninstall: pytz ... (65 packages)

v0.5.45 was supposed to fix exactly this — drop
`--force-reinstall`, use `--upgrade` instead. The fix landed in
`scripts/tarball/netgen-upgrade` in the source repo. But the
script that ACTUALLY runs upgrades on srv06 is at
`/opt/netgen-server/bin/netgen-upgrade`, written by the tarball
installer at server-install time months ago. **Wheel installs
rewrite the venv's site-packages but never touch files outside
the venv.** So v0.5.45's fix sat in the wheel doing nothing
while the months-old --force-reinstall script kept running on
every upgrade.

Full suite: **1,858 passed, 1 skipped** (+11 new tests).

### Fix — bundle + self-heal

1. **Bundle in the wheel.** Copy
   `scripts/tarball/netgen-upgrade` →
   `resources/tarball/netgen-upgrade` and add to
   `pyproject.toml`'s package-data so it ships with the wheel.

2. **Self-heal at startup.**
   `_ensure_netgen_upgrade_script_deployed()` runs at server
   boot:
   - Reads the bundled copy via `importlib.resources.files()`
   - SHA-256-compares against `/opt/netgen-server/bin/netgen-upgrade`
   - If different: backup old → `<dst>.bak.<sha8>`, atomic write
     new (`tmp` + `os.replace`), `chmod 0o755`
   - Idempotent: identical content = no-op (skips on every
     subsequent restart)

3. **Sync test** pins `scripts/tarball/netgen-upgrade` and
   `resources/tarball/netgen-upgrade` byte-identical. A future
   edit that touches one without the other fails CI.

### Catch-22 disclosure

**The v0.5.48 → v0.5.49 upgrade itself will STILL be noisy.**
The self-heal runs in the NEW server process AFTER the upgrade
already landed via the OLD script. There's no way around this —
the script can't update itself mid-execution. But once v0.5.49
is up, the script on disk is refreshed, and v0.5.49 → v0.5.50
(and every upgrade after) will be clean:

| Upgrade | Self-heal status | Upgrade behaviour |
|---|---|---|
| 0.5.47 → 0.5.48 | old script | noisy (uninstall+reinstall 65 pkgs) |
| 0.5.48 → 0.5.49 | **last noisy one** | old script runs, self-heal refreshes for next time |
| 0.5.49 → 0.5.50 | refreshed | clean — only changed packages touched |

### Tests

11 new in `tests/test_v0549_netgen_upgrade_selfheal.py`:
* Wheel ships `resources/tarball/netgen-upgrade`
* `resources/tarball/` is a discoverable package
* Sync test: scripts/tarball/ and resources/tarball/ copies are byte-identical
* `pyproject.toml` lists `resources.tarball` in package-data
* Helper `_ensure_netgen_upgrade_script_deployed()` defined
* Helper called from startup (not dead code)
* Self-heal uses SHA-256 compare (skips when in sync)
* Old script backed up before overwrite (`.bak.<sha8>`)
* Write is atomic via `os.replace()`
* Written script gets `chmod 0o755`
* Version pinned at ≥ 0.5.49

## [0.5.48] - 2026-06-08

**CRITICAL — `/api/dpdk/load_modules` was reporting EVERY
successful load as failed with "Unknown error".** Found during a
fan-out audit the operator requested ("this area is too buggy").
v0.5.46 polished the error wording without realising the error
path was hit on every successful load.

Full suite: **1,847 passed, 1 skipped** (+8 new tests).

### Bug 1 — load_modules fall-through

The `dpdk_load_modules` for-loop had this structure (paraphrased):

```python
for module in modules_to_load:
    try:
        result = subprocess.run([modprobe, module], ...)
        if result.returncode == 0:
            verify_result = subprocess.run([lsmod], ...)
            if verify_result.returncode == 0 and loaded_ok:
                loaded_modules.append(module)
                # ⚠ NO continue
            else:
                failed_modules.append(...)  # ⚠ NO continue
        else:
            failed_modules.append(...)      # ⚠ NO continue
    except (FileNotFoundError, TimeoutExpired, Exception) as e:
        error_msg = str(e)
        # (no append in handlers — intentional)

    # sudo fallback
    if error_msg and module not in loaded_modules and not is_root:
        ...

    # ⚠ UNCONDITIONAL append at the end of the loop body
    failed_modules.append({
        "module": module,
        "error": error_msg or "Unknown error - check server logs"
    })
```

On a successful load:
- `loaded_modules` got `vfio_pci` (correct)
- AND `failed_modules` got `{"module": "vfio_pci", "error":
  "Unknown error - check server logs"}` because control fell
  through to the unconditional bottom append

`failed_modules` non-empty → endpoint returns HTTP 500 →
operator sees "Failed to load modules: vfio-pci: Unknown error".

v0.5.46's journalctl scrape NEVER FIRED because there was no
real subprocess failure — the "Unknown error" string came
directly from the hardcoded fallback in the bottom append.

### Fix

* `continue` after every explicit `failed_modules.append` and
  after the success-path `loaded_modules.append`
* Defense-in-depth guard on the bottom-of-loop append: only
  fires when `module not in loaded_modules AND module not in
  failed_modules`. If a future branch forgets to `continue`,
  we still don't double-record

### Bug 2 — dpdk_bind.sh hardcoded to legacy /opt/OSTG/

Four endpoints (`/api/dpdk/bind`, `/api/dpdk/unbind`,
`/api/dpdk/status`, `/api/dpdk/interfaces`) hardcoded:
```python
dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
```

But the wheel install writes to `/opt/netgen/`. The
`/api/admin/install_dpdk` endpoint already correctly probes both
paths (since v0.5.14-ish). The bind/unbind/status/interfaces
endpoints didn't get the same update.

On srv06 the v0.5.10 `/opt/OSTG → /opt/netgen` compat symlink
saves the day, but on any clean install with no symlink (typical
of fresh netgen-only deployments) all four endpoints return:
```
404 dpdk_bind.sh not found
```

Fix: extracted `_resolve_dpdk_bind_script()` helper that probes
`/opt/netgen/` first, then `/opt/OSTG/` as legacy fallback. All
four endpoints now call the helper.

### Tests

8 new in `tests/test_v0548_load_modules_no_double_append.py`:
* Success path has `continue` after `loaded_modules.append`
* Verify-failed path has `continue`
* modprobe-failed path has `continue`
* Bottom-of-loop append has dedupe guard against double-record
* "Unknown error" literal kept as last-resort fallback
* `_resolve_dpdk_bind_script` helper exists
* Helper probes `/opt/netgen/` BEFORE `/opt/OSTG/`
* All 4 endpoints use the resolver, no remaining hardcoded
  `/opt/OSTG/` literals
* Version pinned at ≥ 0.5.48

## [0.5.47] - 2026-06-08

**Admin console — kernel-driven NICs now get a Bind-to-DPDK
button, not a dangerous Unbind button.** Operator on srv06 saw
this in the admin console interface table:

| Interface | Driver | State | Action button |
|---|---|---|---|
| ens10f3 | tg3 | **Unbound** (yellow) | **Unbind** |
| ens10f1 | tg3 | **Unbound** (yellow) | **Unbind** |
| ens3f0np0 | mlx5_core | DPDK-ready | no bind needed |

Two bugs:

1. **Wrong label.** A NIC with `tg3` kernel driver loaded is
   NOT unbound. The state column was misreporting because
   `/api/dpdk/interfaces` returns `status: 'unbound'` for ANY
   NIC not bound to vfio-pci/uio_pci_generic — which includes
   every kernel-driven NIC.

2. **Dangerous action.** Clicking "Unbind" would hit
   `/api/dpdk/unbind`, detaching the NIC from its current
   driver. For a tg3-driven NIC, that leaves it driverless —
   the interface goes down and stays down until reboot or
   manual rebind.

Full suite: **1,839 passed, 1 skipped** (+8 new tests).

### Fix

Reordered `ifaceState()` checks in the admin HTML JS:

1. **Bifurcated kernel drivers** (mlx5_core, mlx4_core, idxd,
   ioatdma) → DPDK-ready, no action button
2. **vfio-pci / uio_pci_generic / status=dpdk-bound** →
   DPDK-bound, **Unbind** action
3. **NEW: any other real kernel driver** → `Kernel (tg3)` (or
   whichever), **Bind to DPDK** action
4. **Truly unbound** (no driver at all) → `Unbound (no driver)`,
   **Bind to DPDK** action (NOT unbind — can't unbind nothing)

The pre-fix `if (status === 'unbound')` check was too greedy:
it captured both genuinely-driverless NICs AND kernel-driven
NICs. The new branch checks the effective kernel driver first.

### No-PMD warning for tg3 / e1000 / e100

Stock DPDK doesn't have a PMD for the Tigon3-era Broadcom
chips (tg3) or the old Intel 8254x/82559 chips (e1000/e100).
vfio-pci will still claim them — the bind succeeds
mechanically — but no DPDK app will recognize them, so
testpmd / tx_worker would say "no usable device found".

To prevent the operator from chasing this for an hour, the
Bind button now shows a hover tooltip:

> tg3-driven NIC has no DPDK PMD in stock DPDK — vfio-pci bind
> will succeed but DPDK apps won't recognise the device.

The button still renders — the operator may have a custom
PMD or be testing something specific — but they see the
warning BEFORE clicking.

### What srv06 will look like after upgrade

| Interface | Driver | State | Action button |
|---|---|---|---|
| ens10f3 | tg3 | **Kernel (tg3)** (red) | **Bind to DPDK** (with tooltip warning) |
| ens10f1 | tg3 | **Kernel (tg3)** (red) | **Bind to DPDK** (with tooltip warning) |
| ens3f0np0 | mlx5_core | DPDK-ready | no bind needed |

The Mellanox 100G NICs (which are the actual DPDK targets on
srv06) stay unchanged. The Broadcom management NICs now
correctly show their actual state.

### Tests

8 new in `tests/test_v0547_iface_state_kernel_driver_priority.py`:
* Kernel-driven NIC routes to `action: 'bind'` (not `'unbind'`)
* Legacy `status === 'unbound' → action: 'unbind'` branch GONE
* `effectiveKDriver` check runs BEFORE `if (status === 'unbound')`
* Truly-unbound branch (no driver at all) gets `'bind'` action
* NO_PMD set includes tg3 with `DPDK PMD` warning text
* Bind button renders `s.hint` as `title=` tooltip
* vfio-pci NICs still get Unbind button (regression guard)
* Version pinned at ≥ 0.5.47

## [0.5.46] - 2026-06-08

**`/api/dpdk/load_modules` surfaces the actual modprobe error.**
Operator-reported on srv06 (right after v0.5.44 landed):

> HTTP 500
> Failed to load modules: vfio-pci: Unknown error - check server logs

The v0.5.44 systemd-run wrap was applied correctly, the
transient unit was created, and `modprobe vfio-pci` ran inside
it — but the actual error message never reached the operator.

### Why "Unknown error"

The v0.5.44 wrap passed `--quiet` to systemd-run intending to
suppress systemd-run's status output. On some systemd versions
`--quiet` + `--pipe` ALSO suppresses the inner unit's stderr
forwarding — modprobe's "Operation not permitted" message went
to journald instead of subprocess.run. With both subprocess
captures empty, `error_msg` fell through to the literal
`"Unknown error"`.

### Three-layer diagnostic chain

1. **Drop `--quiet`** from the systemd-run modprobe wrap. With
   `--pipe` alone, inner unit stderr flows back to
   subprocess.run normally. Typical case: operator sees the
   actual `modprobe: ERROR: could not insert 'vfio_pci':
   Operation not permitted`.

2. **journalctl scrape fallback.** When subprocess capture is
   still empty, scrape `journalctl -u <unit> --no-pager -n 20
   -o cat` for the transient unit's actual output. Last 5
   non-blank lines surfaced in the API error response. Required
   storing the unit name in a `modprobe_unit` variable so the
   `--unit=` arg and the journalctl query reference the same
   name.

3. **`systemctl status` hint** when both subprocess and
   journalctl come back empty (unit may have failed before
   exec). Error message points at:
   ```
   systemctl status netgen-modprobe-<module>-<ts>.service
   ```

### Before → after

Before v0.5.46:
```
Failed to load modules: vfio-pci: Unknown error - check server logs
```

After v0.5.46 (typical):
```
Failed to load modules: vfio-pci: modprobe: ERROR: could not
insert 'vfio_pci': Operation not permitted
```

After v0.5.46 (journalctl fallback):
```
Failed to load modules: vfio-pci: modprobe rc=1; journalctl
says: modprobe: FATAL: Module vfio_pci not found
```

Full suite: **1,831 passed, 1 skipped** (+7 new tests).

### Tests

7 new in `tests/test_v0546_load_modules_real_error_diagnostic.py`:
* `--quiet` removed from systemd-run wrap
* Unit name stored in `modprobe_unit` variable
* journalctl invocation references `modprobe_unit` via `-u`
* journalctl output trimmed to recent lines
* Final fallback points at `systemctl status <unit>`
* `"Unknown error"` literal removed from initial fallback
* Version pinned at ≥ 0.5.46

v0.5.44 regression test updated to accept variable-style unit
name location.

## [0.5.45] - 2026-06-08

**`netgen-upgrade` drops `--force-reinstall`.** Operator-reported
on srv06 (v0.5.43 → v0.5.44 upgrade): "seems when upgrading the
new version it uninstalls first then installs again.. pls check
if this is necessary?"

Full suite: **1,824 passed, 1 skipped** (+7 new tests).

### The waste

The v0.5.43 → v0.5.44 upgrade log showed **67 packages
uninstalled and reinstalled**. Only **2 actually changed
version**:

* `ostg-trafficgen` 0.5.43 → 0.5.44 — the actual upgrade
* `Flask-Cors` 6.0.4 → 6.0.5 — minor dep update

The other 65 packages went `X.Y.Z` → `X.Y.Z`. Identical version
reinstalled for no reason. ~100 lines of useless `Successfully
uninstalled X` / `Successfully installed X` pairs in the log,
~3 minutes of unnecessary disk churn.

### Cause

`netgen-upgrade` script ran:
```bash
pip install --force-reinstall --no-cache-dir <wheel>
```

`--force-reinstall` forces every dep through the
uninstall→reinstall cycle regardless of whether the installed
version satisfies the new constraint.

The flag was added in v0.4.8 because pip would no-op on
**same-version** installs (a lab-dev rebuild scenario: rebuild a
wheel locally without bumping the version → pip sees same
version → doesn't install the new code). But operator-shipped
upgrades always bump the version, so the original motivation
never applies in production.

### Fix

`pip install --upgrade --no-cache-dir <wheel>` — pip's default
`--upgrade-strategy=only-if-needed` touches only packages whose
installed version doesn't satisfy the new constraint. Untouched
deps keep their existing site-packages entries.

### Lab-dev escape hatch retained

For the rare "I rebuilt a wheel with the same version" case,
pass `--force-reinstall` on the CLI:

```bash
sudo netgen-upgrade --force-reinstall my-rebuilt-0.5.45.whl
```

The script detects the flag in argv and re-enables the v0.4.8
behaviour for that one invocation.

### Outcome

| Scenario | Before v0.5.45 | After v0.5.45 |
|---|---|---|
| Normal release upgrade (vX → vY) | 67 packages uninstalled+reinstalled | Only changed packages touched (typically 2-5) |
| Log volume | ~140 lines | ~10-20 lines |
| Wall-clock | ~3 min | ~30s |
| Disk write churn | full reinstall of every dep | only the changed wheels |

### Tests

7 new in `tests/test_v0545_netgen_upgrade_no_force_reinstall.py`:
* Default path uses `--upgrade` (not `--force-reinstall`)
* `--force-reinstall` literal absent from default pip command
* `--force-reinstall` CLI flag still parsed from argv
* CLI flag actually appends to pip_cmd (not just parsed)
* `--no-cache-dir` preserved across the refactor
* Rationale comment present (operator-complaint + upgrade-strategy
  reference)
* Version pinned at ≥ 0.5.45

## [0.5.44] - 2026-06-08

**`/api/dpdk/load_modules` wraps modprobe in `systemd-run` to
escape `ProtectKernelModules=true`.** Operator-reported on srv06
admin console:

```
HTTP 500
Failed to load modules: vfio-pci: modprobe: ERROR: could not
insert 'vfio_pci': Operation not permitted
```

Full suite: **1,817 passed, 1 skipped** (+7 new tests).

### Root cause

netgen-server.service ships with `ProtectKernelModules=true`
(systemd hardening). The kernel rejects `init_module()` syscalls
from processes inside the locked-down cgroup regardless of UID
— so modprobe gets EPERM the moment it tries to insert.

The error message itself listed the cause:
> Systemd service has ProtectKernelModules=true (check systemd
> service file)

Just like v0.5.31's apt setgroups EPERM and v0.5.33's apt
SetupAPTPartialDirectory chmod failure, this is netgen-server's
own sandbox blocking a legitimate kernel-touching operation
the service needs to perform.

### Fix — same cgroup-escape pattern as v0.5.33

Wrap the modprobe call in `systemd-run --wait --pipe --collect`:

```python
modprobe_cmd = [
    "systemd-run",
    "--wait", "--pipe", "--collect", "--quiet",
    f"--unit=netgen-modprobe-{module_safe}-{ts}.service",
    "--",
    modprobe_path, module,
]
```

systemd-run spawns the modprobe in a fresh transient unit with
vanilla defaults (no inherited `ProtectKernelModules`). The
kernel allows `init_module()` from there.

* `--wait` — caller still sees the exit code via `proc.poll()`
* `--pipe` — modprobe stdout/stderr reaches the API response
* `--collect` — transient unit auto-removes on exit (no
  accumulation in `systemctl list-units`)
* Per-invocation unit name (module + timestamp) — back-to-back
  loads (vfio + vfio-pci) don't collide

### Fallback for non-systemd hosts

When `_systemd_run_available()` returns None (Docker, macOS dev,
non-root), the endpoint falls back to bare modprobe. On those
hosts the sandbox doesn't apply anyway.

### Subprocess timeout bumped 10s → 15s

systemd-run adds ~100-200ms of unit setup/teardown overhead.
The bump gives headroom for legitimately-slow loads (kernel
symbol resolution on aged hosts).

### Tests

7 new in `tests/test_v0544_modprobe_systemd_run_escape.py`:
* `dpdk_load_modules` probes for systemd-run availability
* systemd-run wrap uses `--wait` + `--pipe` + `--collect`
* Unit name is per-invocation (f-string, references module
  + timestamp — anti-collision)
* Falls back to bare `[modprobe_path, module]` when
  `_systemd_run_available()` returns None
* `subprocess.run` uses the constructed `modprobe_cmd` variable
  (not the original literal — anti-regression on copy-paste)
* `subprocess.run` timeout ≥ 15s (covers systemd-run overhead)
* Version pinned at ≥ 0.5.44

### Operator unblock

Once v0.5.44 is on srv06, clicking `Load VFIO Modules` from the
admin console actually loads `vfio_pci`. Until then, manual:

```bash
ssh root@san-hp-srv06 'modprobe vfio-pci'   # bypasses the
                                             # netgen-server cgroup
```

## [0.5.43] - 2026-06-08

**Link status uses sysfs operstate + admin response drives icon
immediately.** Operator-reported on srv06: "tried online
interface, link is online but GUI link status shows red." Two-
layer bug — both the server's status read and the client's
post-action refresh schedule needed fixing.

Full suite: **1,810 passed, 1 skipped** (+9 new tests).

### Layer 1 — Server: psutil.isup is wrong for fast queries

`psutil.net_if_stats().isup` on Linux returns `IFF_UP AND
IFF_RUNNING`. The `IFF_RUNNING` flag tracks **carrier** —
takes 2-10s on big NICs (Mellanox 100G especially) to come up
after `ip link set up`. So a freshly-upped link reports "down"
via psutil for several seconds even though `ip link show` says
`state UP` (which reads from `operstate`).

**Fix:** `/api/interfaces` now reads
`/sys/class/net/<iface>/operstate` as the primary signal:

| operstate | status returned |
|---|---|
| `up` | `up` |
| `down` | `down` |
| `unknown` (virtual / loopback / driver doesn't report) | `up` — operator-requested admin state respected |

Falls back to `psutil.isup` when the sysfs read fails (macOS dev
hosts, container with proc-only mounts, etc.).

Response now also exposes the raw `operstate` field so clients
can use it for debug or directly drive icons.

### Layer 2 — Client: admin response thrown away

The right-click `Set Online` action POSTed to
`/api/interfaces/<iface>/admin` and the response already
carried `operstate` from the **same** sysfs source. The client
showed it in the QMessageBox then **threw it away** — the icon
update waited for the next `/api/interfaces` poll, which (pre-
Layer 1) was wrong anyway.

**Fix:** new `_update_iface_icon_for_operstate(server_addr,
iface, operstate)` helper walks the server tree, finds the
matching port_item, applies the green/red dot icon immediately
from the admin response's operstate. The polling cycle becomes
a backup, not the primary path.

### Layer 3 — Single refresh missed slow carrier

The Set Online handler scheduled **one** `QTimer.singleShot(500,
self.update_server_tree)`. Even with the operstate fix, a 500ms-
later poll could still mid-negotiation. **Fix:** staggered to
500ms + 3s + 8s, covering Mellanox 100G's worst case.

### Outcome

| Scenario | Before v0.5.43 | After v0.5.43 |
|---|---|---|
| Set Online on fast NIC (Intel/Broadcom 10G) | Icon flips green ~1s later (lucky polling) | Icon flips green ~immediately (admin response) |
| Set Online on Mellanox 100G (slow carrier) | Icon stays red for 8-10s, sometimes indefinitely | Icon green immediately from admin response; staggered polls confirm |
| Virtual / loopback iface | Icon may stay red (operstate=unknown → psutil.isup=False) | Icon green (unknown treated as up) |
| Cable disconnected | Eventually red after polling | Eventually red after polling (correct) |

### Tests

9 new in `tests/test_v0543_link_status_operstate.py`:
* `/api/interfaces` reads `/sys/class/net/<iface>/operstate`
* `operstate=unknown` treated as up (virtual ifaces)
* Falls back to `psutil.isup` on sysfs read failure
* Response exposes `operstate` field
* Set Online handler calls `_update_iface_icon_for_operstate`
* Helper is defined (no AttributeError on click)
* Helper treats `unknown` as up consistently with server
* Handler schedules ≥ 3 `QTimer.singleShot` calls + at least one
  past 5s (covers slow carrier)

## [0.5.42] - 2026-06-08

**`/api/dpdk/load_modules` verify uses anchored regex (not
substring).** Operator-reported on srv06 (kernel 6.8.0-124):
admin console "Load VFIO Modules" toast said "VFIO modules
loaded" while the Status row continued to show
`vfio_pci module: Not loaded`.

Full suite: **1,801 passed, 1 skipped** (+6 new tests).

### The bug

Post-modprobe verify used a SUBSTRING match:

```python
if module_pattern in verify_result.stdout.lower():  # ← bug
    loaded_modules.append(module)
```

`module_pattern = "vfio_pci"` substring-matches inside:
* `vfio_pci_core` — always present when `pds_vfio_pci` is
  loaded (AMD Pensando auto-loaded on srv06's hardware)
* `pds_vfio_pci` itself

So `modprobe vfio-pci` could rc=0 without actually loading the
bare `vfio_pci` module — verify still reported success →
admin toast lied → operator was confused why Status disagreed.

### Why it diverged from Status

The **same endpoint's** skip-already-loaded check (a few lines
above) used the anchored regex `^{module_pattern}\s` and worked
correctly. The status endpoint `/api/dpdk/status` also used
anchored regex. Only the post-modprobe verify diverged — a
copy-paste mistake that lived since the original endpoint.

### Fix

Verify now uses the same anchored regex as the skip check:

```python
loaded_ok = bool(re.search(
    rf'^{module_pattern}\s',
    verify_result.stdout,
    re.MULTILINE | re.IGNORECASE,
))
```

### Diagnostic context when verify fails

When the anchored verify catches a false-success (modprobe rc=0
but module isn't there), the error message now includes:

* **Related lsmod entries** — enumerates which modules from the
  same family ARE loaded (e.g. `vfio_pci_core; pds_vfio_pci;
  vfio`), so the operator sees what's hogging the slot.
* **modprobe stderr** — kernel rejection reasons (signing,
  blacklist, version mismatch) reach the admin toast directly
  instead of being lost to journalctl.
* **Likely cause hint** — calls out kernel 6.8+ vfio-pci split
  + pds_vfio_pci as the most common cause, with a copy-pasteable
  `modprobe -v vfio-pci` diagnostic command.

Operator can self-resolve from the admin console without SSHing.

### Tests

6 new in `tests/test_v0542_load_modules_verify_anchored.py`:
* Verify uses anchored `re.search(rf'^{module_pattern}\s', ...)`
* Buggy `module_pattern in verify_result.stdout.lower()` is GONE
* Failure error message enumerates related lsmod entries
  (`Related lsmod ...`)
* Failure error message includes modprobe `stderr`
* Skip-already-loaded check still uses the anchored regex
  (anti-regression — would over-skip and never attempt modprobe)
* Version pinned at ≥ 0.5.42

## [0.5.41] - 2026-06-08

**Install/Upgrade dialog log header compacted.** Operator
follow-up to v0.5.40: "seems you are taking too much vertical
space for pop out button, also i see Ready somewhere in the
text area."

Full suite: **1,795 passed, 1 skipped** (+6 new tests).

### What was wrong

Two issues from the screenshot, both rooted in the same layout
mistake (using two rows where one would do):

1. **Pop out button row took ~150px.** QGroupBox's default 11px
   contentsMargins + QVBoxLayout's default 9px spacing + the
   button's natural 30px height left a giant empty gray strip
   around an otherwise small button.

2. **"Ready." rendered inside log_view's bottom border.** The
   `status_lbl` was in its own row immediately below
   `log_view` — the label's gray text sat AT the boundary
   between log_view's white background and the dialog's gray
   surround. Operator read it as "text inside the log area".

### Fix — one row instead of two

```
v0.5.40:                           v0.5.41:
  log_header [ Pop out ↗ ]           log_header [ Ready. — — — — Pop out ↗ ]
  log_view (white)                   log_view (white)
  status_lbl [ Ready. ]              (no separate status row)
```

* `status_lbl` moved into the same `QHBoxLayout` as `popout_btn`,
  added with stretch=1 so it grows horizontally and pushes the
  Pop out button to the right edge.
* Separate `log_layout.addWidget(self.status_lbl)` call below
  log_view removed.
* `log_layout.setContentsMargins(8, 14, 8, 8)` — trim the
  group-box internal padding (was Qt default 11px on all sides).
* `log_layout.setSpacing(4)` — tighter inter-row spacing.
* `popout_btn.setMaximumHeight(28)` — button can't stretch to
  fill the row.
* `status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)`
  — operator can select + copy the progress message if it
  contains a path or URL.

### Net effect

log_view gets ~40px more vertical space because the second row
is gone, AND the operator gets a clean single header row with
status (left) + Pop out (right).

### Tests

6 new in `tests/test_v0541_install_log_header_compact.py`:
* `log_layout.setContentsMargins(...)` — all four values ≤ 14px
* `status_lbl` constructed + added inside `log_header` block
  (not in its own row below log_view)
* `status_lbl` added BEFORE `popout_btn` (status left, button
  right)
* `log_layout.addWidget(self.status_lbl, ...)` is **NOT**
  present anywhere — the separate row is gone
* `popout_btn.setMaximumHeight(...)` — button can't stretch
* `status_lbl` added with stretch ≥ 1 — anchors Pop out to the
  right edge

## [0.5.40] - 2026-06-08

**Install/Upgrade Server dialog log_view is taller by default.**
Operator request: "increase the log text area vertical size,
inside install/upgrade server dialog."

Full suite: **1,789 passed, 1 skipped** (+6 new tests).

### Sizing changes

| Knob | Before | After |
|---|---|---|
| Dialog `setMinimumSize` | 820×600 | 900×780 |
| `log_view.setMinimumHeight` | (none) | 280px |
| `log_view.setSizePolicy` | (default) | Expanding × Expanding |
| `log_layout.addWidget` stretch | 0 | 1 |

### Outcome

| Dialog state | Pre-v0.5.40 visible log lines | v0.5.40 |
|---|---|---|
| Default-opened (operator launch) | ~12-15 lines | ~36 lines |
| Resized to minimum | ~5 lines | ~25 lines (the 280px floor) |
| Resized to a tall window | grows but slowly (no stretch) | grows freely (stretch=1 + Expanding) |

### Tests

6 new in `tests/test_v0540_install_dialog_log_view_size.py`:
* Dialog `setMinimumSize` is ≥ 900×720
* `log_view.setMinimumHeight` is ≥ 240px
* `log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)`
* `log_layout.addWidget(self.log_view, N)` has stretch ≥ 1
* `QSizePolicy` imported (no NameError on construction)
* Version pinned at ≥ 0.5.40

## [0.5.39] - 2026-06-08

**DPDK install audit — 4 gaps closed.** Operator request after
the v0.5.38 close-out: "audit dpdk install and make sure all
the steps are taken care, we need to provide user simple and
easy install experience".

Full suite: **1,783 passed, 1 skipped** (+14 new tests, 1
existing test pattern updated).

### Gap 1 — `/mnt/huge` mount lost on reboot

Pre-v0.5.39, `/api/dpdk/hugepages` ran `mount -t hugetlbfs
nodev /mnt/huge` but never wrote `/etc/fstab`. Reboot → mount
gone → DPDK apps fail with `"no free hugepages"` even though
`/proc/meminfo HugePages_Total > 0` (v0.5.37 persists the
count via sysctl.d).

**Fix:** append `nodev /mnt/huge hugetlbfs defaults 0 0` to
`/etc/fstab` on the first successful mount. Idempotent (skips if
the entry's already there).

### Gap 2 — NIC vfio-pci bind lost on reboot

`dpdk-devbind.py` writes runtime sysfs only — reboot returns
the NIC to its kernel driver. Operator who'd just bound a NIC
via Make DPDK Ready found "0 NICs bound" on next boot.

**Fix:** persistence registry + systemd oneshot unit.

* `/etc/netgen/dpdk-interfaces.json` — registry of every bound
  NIC (PCI address + driver + iface name + bind timestamp).
  Updated on every `/api/dpdk/bind` success; entry removed on
  `/api/dpdk/unbind` success.
* `/etc/systemd/system/netgen-dpdk-rebind.service` — oneshot
  unit, ordered `After=systemd-modules-load.service` so vfio-pci
  is loaded BEFORE the helper runs.
* `/usr/local/sbin/netgen-dpdk-rebind` — Python helper script
  that reads the registry + calls `dpdk-devbind.py --bind=<drv>
  <pci>` for each entry on boot. Falls back gracefully if
  `dpdk-devbind.py` isn't installed yet (e.g., before DPDK
  install completes).

Boot order:

```
systemd-modules-load.service     # loads vfio-pci (v0.5.37)
   └─ netgen-dpdk-rebind.service # re-binds NICs from registry
       └─ netgen-server.service  # starts last (uses the binds)
```

Bind endpoint auto-installs the unit + helper on first bind
(idempotent — skips if files exist). No netgen-install step
change needed; existing tarballs / wheels pick up the behaviour
on the next operator bind.

### Gap 3 — Diagnostics didn't surface `/mnt/huge` mount state

Pre-fix, Diagnostics had a single `✓ Hugepages configured` row
that read `/sys/.../nr_hugepages`. A mount-missing host would
show ✓ on that row but still fail at DPDK app startup.

**Fix:** `/api/dpdk/status` response gains
`hugepages_mounted: bool` + `hugepages_mount_point: str`
(scraped from `/proc/mounts` for `hugetlbfs`). Diagnostics dialog
renders a new row `Hugepages mounted (/mnt/huge)` separate from
the count check.

### Gap 4 — "Already ready" UX showed empty action list

When the orchestrator surveyed and found nothing pending,
MakeDpdkReadyDialog set _detail to
`"✓ DPDK is already ready"` and Run button to `"Nothing to do"`
(disabled). Empty action list. Operator confusion: "what does
ready mean? what's actually installed? how do I add a NIC?"

**Fix:** the already-ready branch now renders a positive summary
in the QTextBrowser:

```
✓ DPDK is already ready on this server.

DPDK version: 23.11.0
IOMMU: Intel IOMMU enabled (passthrough mode)

Current state:
  ✓ DPDK installed (libdpdk)
  ✓ tx_worker binary
  ✓ Hugepages allocated
  ✓ Hugepages mounted (/mnt/huge)
  ✓ IOMMU enabled in kernel
  ✓ vfio module loaded
  ✓ vfio-pci module loaded
  NICs bound to vfio-pci: 2

If you want to bind a (different) NIC, click Bind another NIC… —
the wizard will show only the bind step. Otherwise close this dialog.
```

The Run button is re-labelled **Bind another NIC…** and stays
**enabled** so the operator can re-bind a newly-added NIC without
closing + re-opening the dialog.

### Tests

14 new in `tests/test_v0539_dpdk_install_audit.py`:
* fstab fix: `/etc/fstab` reference + `hugetlbfs` filesystem type
  + idempotent (reads existing content before append)
* Bind persistence: `_dpdk_persist_bind` + `_dpdk_unpersist_bind`
  helpers exist
* Registry at canonical `/etc/netgen/dpdk-interfaces.json`
* Systemd unit `netgen-dpdk-rebind.service` reference + installer
  function
* Unit orders `After=systemd-modules-load.service` (won't race)
* Helper at canonical `/usr/local/sbin/netgen-dpdk-rebind`
* `/api/dpdk/bind` calls `_dpdk_persist_bind` on success
* `/api/dpdk/unbind` calls `_dpdk_unpersist_bind` on success
* `/api/dpdk/status` includes `hugepages_mounted` from
  `/proc/mounts`
* Diagnostics dialog renders the `Hugepages mounted` row
* Already-ready dialog shows summary (not empty list)
* Already-ready Run button stays enabled + relabelled
  `Bind another NIC…` so re-bind flow remains accessible

1 existing test updated: `test_system_deps_auto_install::test_run_tgen_server_wires_in_autoinstall`
now takes the LAST `^def main(` match in the file (the real
entry-point) instead of the first — pre-fix the embedded helper
script's own `def main():` inside a raw-string literal would
match first and the test'd look at the wrong function body.

### Recovery for existing servers (manual SSH, no wheel upgrade needed)

```bash
ssh root@<server> 'bash -s' <<'EOF'
set -e
# 1. fstab for /mnt/huge
grep -q hugetlbfs /etc/fstab || \
    echo "nodev /mnt/huge hugetlbfs defaults 0 0" >> /etc/fstab

# 2. Hugepages + vfio persistence (from v0.5.37 — idempotent)
grep -q vm.nr_hugepages /etc/sysctl.d/99-netgen-hugepages.conf 2>/dev/null || \
    echo "vm.nr_hugepages = 1024" > /etc/sysctl.d/99-netgen-hugepages.conf
test -f /etc/modules-load.d/netgen-dpdk.conf || \
    printf 'vfio\nvfio-pci\n' > /etc/modules-load.d/netgen-dpdk.conf

# 3. Verify all 3 persist
sysctl --system >/dev/null
modprobe vfio vfio-pci
mountpoint -q /mnt/huge || mount -a
grep HugePages_Total /proc/meminfo
lsmod | grep -E '^vfio|^vfio_pci'
mountpoint /mnt/huge
EOF
```

For NIC bind persistence (Gap 2), the server needs to be on
v0.5.39 — the registry + systemd unit are auto-installed by
`/api/dpdk/bind` itself, so upgrading the wheel and doing one
new bind via the GUI sets it all up.

## [0.5.38] - 2026-06-08

**`ip link set` arg order — drop `--`, use `dev`.** Operator
right-click Set Online on the Interfaces tab returned HTTP 500:

```
{"error": "Error: either \"dev\" is duplicate, or \"ens3f0np0\"
 is a garbage.", "ok": false}
```

Full suite: **1,769 passed, 1 skipped** (+6 new tests, 1
existing test inverted).

### Root cause: iproute2 doesn't grok GNU `--`

The v0.5.4 `interface_admin` endpoint constructed:

```python
["ip", "link", "set", "--", iface, state]
```

The `--` end-of-options separator is a GNU convention — `getopt` /
`coreutils` honour it, iproute2 does NOT. When `ip link set` sees
`--` it tries to parse it as a positional argument, collides with
the next arg's role as device name, and errors out with that
exact "either dev is duplicate, or X is a garbage" message.

The original v0.5.4 intent was right (defend against iface names
starting with `-` being parsed as flags). The implementation was
wrong (wrong tool's convention).

### Fix: explicit `dev` keyword

```python
["ip", "link", "set", "dev", iface, state]
```

iproute2's canonical disambiguation. `dev` tells `ip link set`
that the next arg is the device name, regardless of whether it
starts with `-`. Same defense the `--` was trying to provide,
but in the dialect iproute2 actually speaks.

### Tests

6 new in `tests/test_v0538_interface_admin_iproute2_arg_order.py`:
* Subprocess call uses the `dev` keyword
* Subprocess call does NOT use the `--` separator
* `dev` appears before `iface` in the arg list (`set dev <iface> <state>`,
  not the wrong order)
* Subprocess remains list-form (anti-regression on shell-injection
  safety — no `shell=True`)
* No other `ip ...` callsites pass `--` (sweep across the
  repo)

1 existing test inverted: `test_v054_interface_admin_context_menu::test_interface_admin_uses_double_dash_to_separate_args`
renamed to `test_interface_admin_uses_dev_keyword_not_double_dash`.
Pre-v0.5.38 it enforced the `--` (the bug); v0.5.38 enforces `dev`
+ absence of `--`. Comment in the test explains the inversion +
links to the v0.5.38 fix.

## [0.5.37] - 2026-06-08

**DPDK runtime state survives reboots.** Operator on srv06 after
testing the v0.5.34 Reboot Server button:

```
Diagnostics:
  ✓ DPDK installed (libdpdk)
  ✓ tx_worker binary
  ✗ Hugepages configured       ← regressed
  ✓ IOMMU enabled in kernel
  ✓ vfio module loaded
  ✗ vfio-pci module loaded     ← regressed
```

Both regressed because `/api/dpdk/hugepages` and
`/api/dpdk/load_modules` made runtime-only changes — no
persistence files written. Reboot wiped the sysfs allocations
and the modprobe state.

Full suite: **1,763 passed, 1 skipped** (+9 new tests).

### Why install_dpdk.sh's persistence didn't help

`install_dpdk.sh` writes
`/etc/sysctl.d/99-netgen-hugepages.conf` itself. But the orchestrator's
"Allocate 1024 × 2MB hugepages" and "Load vfio + vfio-pci kernel
modules" steps **bypass install_dpdk.sh entirely** — they hit
the REST endpoints directly, which were runtime-only.

Pre-v0.5.37 paths:

| Endpoint | What it did | Persistence |
|---|---|---|
| `/api/dpdk/hugepages` | `echo N > /sys/.../nr_hugepages` + mount /mnt/huge | **none** |
| `/api/dpdk/load_modules` | `modprobe vfio && modprobe vfio-pci` | **none** |

Both relied on the operator never rebooting after Make DPDK
Ready ran. The v0.5.34 Reboot Server button made the regression
trivially reproducible.

### Fix

Both endpoints now write canonical systemd persistence files at
the end of their success path:

**`/api/dpdk/hugepages` → `/etc/sysctl.d/99-netgen-hugepages.conf`**

```
# /etc/sysctl.d/99-netgen-hugepages.conf
# Written by netgen-server /api/dpdk/hugepages (v0.5.37).
vm.nr_hugepages = 1024
```

systemd-sysctl re-applies on every boot. Same path used by
install_dpdk.sh's standalone hugepages step.

**`/api/dpdk/load_modules` → `/etc/modules-load.d/netgen-dpdk.conf`**

```
# /etc/modules-load.d/netgen-dpdk.conf
# Written by netgen-server /api/dpdk/load_modules (v0.5.37).
vfio
vfio-pci
```

systemd-modules-load auto-loads on boot.

### Best-effort semantics

Both writes are wrapped in try/except. If `/etc` isn't writable
(read-only root, container, restrictive sandbox), the runtime
change has ALREADY succeeded — we don't 500 on a persistence
failure. Instead:

1. Log a clear warning naming the path that wasn't written
2. Return success with `persisted: false` in the JSON so the
   client can warn the operator their config won't survive a
   reboot

Both responses gained a `persisted: bool` + `persist_path: str|null`
field for client-side visibility.

### Recovery for srv06 right now (no wheel upgrade needed)

```bash
ssh root@san-hp-srv06 'bash -s' <<'EOF'
set -e
# Hugepages persistence
echo "vm.nr_hugepages = 1024" > /etc/sysctl.d/99-netgen-hugepages.conf
sysctl --system >/dev/null

# vfio modules persistence
printf 'vfio\nvfio-pci\n' > /etc/modules-load.d/netgen-dpdk.conf
modprobe vfio vfio-pci

# Verify
echo "HugePages_Total: $(grep HugePages_Total /proc/meminfo | awk '{print $2}')"
lsmod | grep -E '^vfio|^vfio_pci'
EOF
```

After running that, `Tools → DPDK → Diagnostics` should flip both
to ✓. The state will now survive every future reboot — even
WITHOUT upgrading to v0.5.37, because the files exist on disk.

### Tests

9 new in `tests/test_v0537_dpdk_runtime_state_persistence.py`:
* `/api/dpdk/hugepages` writes `/etc/sysctl.d/99-netgen-hugepages.conf`
  with `vm.nr_hugepages = N`
* Persistence write happens AFTER the sysfs allocation (no
  persisting a kernel rejected count)
* Persistence write wrapped in try/except + logs warning on
  failure (best-effort semantics)
* Success response includes `persisted: bool` field
* `/api/dpdk/load_modules` writes
  `/etc/modules-load.d/netgen-dpdk.conf`
* Persistence iterates `modules_to_load` (writes EACH module to
  its own line)
* Persistence write wrapped in try/except + logs warning
* Success response includes `persisted: bool` field
* Version pinned at ≥ 0.5.37

## [0.5.36] - 2026-06-08

**Make DPDK Ready detail pane stays in sync with the action row
on NIC-bind cancel.** Operator screenshot from srv06 showed the
action row correctly grayed `— cancelled by operator`, but the
detail pane below still read `Running: Bind a NIC to vfio-pci…`.
Operator-confusing — looked like the dialog was still working
when it had stopped.

Full suite: **1,754 passed, 1 skipped** (+7 new tests).

### Bug

`_run_action()` calls `self._detail.setText(f"Running: {action.label}…")`
when an action starts. The NIC-bind action then opens a picker
dialog (synchronous `picker.exec_()`). When the operator cancels
the picker:

```python
if picker.exec_() != QDialog.Accepted:
    row.set_state("skip", "cancelled by operator")
    self._run_btn.setText("Run All Steps")
    self._run_btn.setEnabled(True)
    return                                            # ← _detail stays at "Running: …"
```

`row.set_state(...)` updates the action LIST row. But `_detail`
(the wider status pane below the list, switched to QTextBrowser
in v0.5.32) was never updated — operator continued to see
`Running: Bind a NIC to vfio-pci…` even though nothing was
running.

Same bug in the empty-selection branch (`row.set_state("fail",
"no NIC selected")`).

### Fix

Update `_detail.setText(...)` in both branches with state-specific
recovery text:

* **Cancel branch:**
  > NIC bind cancelled. No NIC was bound to vfio-pci. DPDK
  > installation is complete but no interface is available for
  > high-rate TX. To bind later, click *Run All Steps* again, or
  > use **Tools → DPDK → Advanced → Bind Interface…**.

* **No-selection branch:**
  > No NIC selected. The picker returned an empty selection.
  > Click *Retry* to choose a NIC from the dropdown, or *Cancel*
  > to abort.

Both texts point at the recovery path so the operator isn't
stranded.

### Tests

7 new in `tests/test_v0536_make_ready_cancel_detail_text.py`:
* Cancel branch calls `self._detail.setText(...)`
* Cancel text mentions cancellation (not just blank)
* Cancel text points at recovery path (Run All Steps / Bind
  Interface… / Advanced)
* No-selection branch calls `self._detail.setText(...)`
* No-selection text mentions Retry / "choose a NIC"
* Cancel branch still calls `row.set_state("skip", ...)` (anti-
  regression on the v0.5.36 build-on layer)
* Version pinned at ≥ 0.5.36

Test-helper note: the no-selection block extractor anchors the
trailing `return` on word-boundary + start-of-line because the
new prose text contains the literal `"The picker returned an
empty selection."` — naive `[\s\S]+?return` matched the substring
`return` inside the literal and stopped early.

## [0.5.35] - 2026-06-08

**Canonical 7-phase user workflow folded into Help → Feature
Guide.** Operator request after v0.5.34 close-out: "fold this
into the in-app help guide".

Full suite: **1,747 passed, 1 skipped** (+9 new tests).

### What landed

A new <b>User workflow</b> section at the TOP of
<code>_FEATURE_GUIDE_HTML</code> — appears immediately after
the guide's <h1> intro, before the version-by-version
highlights. New operators opening Help → Feature Guide see the
canonical flow FIRST, not a list of v0.3.13 bug fixes.

### The 7 phases

| Phase | What | Menu path |
|---|---|---|
| 1 | Install netgen-server on host | scp tarball → `netgen-install` |
| 2 | Connect from desktop client | `Add TGen Chassis` → `<host>:5050` |
| 3 | Provision the server (optional) | `Setup RDMA` / `Setup DPDK` (order matters for Mellanox) |
| 4 | Add devices + streams | `Devices tab → +`, `Streams tab → +` |
| 5 | Run traffic | `Start ▶` per-stream |
| 6 | Specialised tests | RFC 2544, Blast RDMA, Topology, L2 emul, Stateful TCP |
| 7 | Upgrade later | `Install Server → Upgrade Running Server` |

Plus a compressed mental-model ASCII diagram showing the flow
end-to-end at a glance.

### Discoverability fixes embedded in the workflow

* **Mellanox order dependency** — Setup RDMA must run BEFORE
  Setup DPDK so the mlx5 PMD picks up libibverbs at meson
  configure time. The workflow surfaces this explicitly so
  operators don't hit the silent-PMD-skip trap.
* **Reboot prompt** — Setup DPDK reboots the host when IOMMU is
  enabled; the workflow tells operators to re-open Make DPDK
  Ready post-reboot (orchestrator auto-skips already-done steps).
* **Wheel upgrade IS the canonical update path** — tarball is
  for fresh installs only. The workflow says so.
* **Recent UX wins cited inline** — scrollable log (0.5.32),
  inline apt-fail tail (0.5.30), Reboot Server button (0.5.34),
  cgroup-detached upgrade (0.5.23), state-loss-resilient client
  (0.5.24). Operators reading the guide know these features
  exist.

### Tests

9 new in `tests/test_v0535_workflow_in_feature_guide.py`:
* `User workflow` <h2> section present
* Section appears BEFORE version highlights (positional check)
* All 7 phases have <h3>Phase N — ...</h3> headers
* Setup RDMA + Setup DPDK menu paths cited verbatim
* Mellanox order dependency surfaced (Setup RDMA FIRST)
* Phase 7 references "Upgrade Running Server"
* Compressed mental model present + has all key nodes
  (fresh host, Add TGen Chassis, Devices, Streams, Upgrade)
* At least 2 recent-UX version stamps (0.5.23 / 24 / 30 / 32 /
  34) cited near the workflow
* Version pinned at ≥ 0.5.35

## [0.5.34] - 2026-06-08

**DPDK menu consolidation + persistent Reboot Server button.**
Operator request after the v0.5.33 install close-out: "make
[Configure Hugepages + Configure IOMMU] part of same install
process make dpdk ready, and allow user to reboot the server
from same window."

Full suite: **1,738 passed, 1 skipped** (+11 new tests, 1
existing test pattern updated for the menu reshape).

### Menu consolidation

`Tools → DPDK → Advanced` had four items pre-v0.5.34:

```
Tools → DPDK → Advanced
  ├─ Quick Start Wizard...
  ├─ Bind Interface...
  ├─ Unbind Interface...
  ├─ Configure Hugepages...   ← REMOVED
  ├─ Configure IOMMU...        ← REMOVED
  └─ Load VFIO Modules
```

`Configure Hugepages` and `Configure IOMMU` are already handled
by `install_dpdk.sh` — hugepages at Step 7, IOMMU at Step 7 with
the v0.5.15 inline reboot prompt. The standalone items were a
**divergent-paths trap**:

* Operator runs `Configure IOMMU` out-of-band → script edits
  `/etc/default/grub` but doesn't prompt for reboot
* Operator runs `Make DPDK Ready` later → orchestrator can't tell
  whether the manual IOMMU run completed
* IOMMU state drifts between what the orchestrator thinks and
  what's actually in the kernel cmdline

Removing them eliminates the divergence. Hugepages and IOMMU are
always set up by `Make DPDK Ready` in the right order. State
surfacing happens via `Diagnostics` (which reads the same state
the orchestrator does, so any drift is visible).

`Load VFIO Modules` stays — it's the one Advanced action with a
legitimate standalone use case (custom-kernel hosts where Make
DPDK Ready's `modprobe` doesn't auto-persist across boots).

The standalone Python handlers (`configure_hugepages`,
`configure_iommu`) remain in the codebase for any external
caller — only the GUI wiring is removed. No behavior change for
anything that called them programmatically.

### Persistent "Reboot Server…" button

v0.5.15 added an inline reboot prompt that fired AFTER an IOMMU
step succeeded. That covered the canonical path but missed
several legitimate cases:

* Operator manually edited `/etc/default/grub` and wants to
  reboot to test
* Operator ran Setup DPDK earlier without IOMMU prompt (because
  IOMMU was already set) but needs to reboot for a different
  reason — kernel module sticky state, sysctl change, etc.
* Operator wants to verify DPDK survives a reboot

The new `Reboot Server…` button is in the dialog footer,
between `Unbind NIC…` and `Close`. Visible at all times.

Click flow:
1. Generic confirmation dialog ("Reboot $host now?")
2. Warning about in-flight TGen sessions / RDMA tests / DPDK
   installs
3. On confirm: POST `/api/system/reboot` (the v0.5.2 endpoint)
4. Server replies, then schedules systemd reboot ~3 s later

`_on_reboot_request` is a SEPARATE method from v0.5.15's
`_prompt_reboot` — different messages, different triggers. The
IOMMU-step prompt still fires when an IOMMU action succeeds;
the manual button fires whenever the operator clicks it.

### Why explicit confirmation (no one-click reboot)

A full-host reboot terminates everything: in-flight TGen
sessions (RFC 2544 runs, multi-stream tests), RDMA perftest
jobs, ongoing DPDK installs, debug captures. One-click would be
too destructive. The confirmation also calls out an important
v0.5.33 caveat — the install_dpdk transient systemd unit
survives a netgen-server restart but NOT a full host reboot,
so the operator must cancel any in-flight install first.

### Tests

11 new in `tests/test_v0534_dpdk_menu_consolidation.py`:
* `QAction("Configure Hugepages...")` no longer added to
  Advanced submenu
* `QAction("Configure IOMMU...")` no longer added to Advanced
  submenu
* `Load VFIO Modules` still added (regression guard)
* Removal accompanied by a comment block referencing v0.5.34 +
  consolidation / Make DPDK Ready
* `self._reboot_btn` constructed as QPushButton
* Reboot button added to `btns` (button box)
* Button's `.clicked` connected to `_on_reboot_request`
* `_on_reboot_request` slot is defined (no AttributeError on
  click)
* Handler calls `_trigger_reboot()` on confirm
* Handler shows QMessageBox + gates the actual reboot on
  `clickedButton() is reboot_btn` (Cancel doesn't reboot)
* Both `_prompt_reboot` (v0.5.15 IOMMU prompt) and
  `_on_reboot_request` (v0.5.34 manual button) exist

1 existing test updated: `test_v0518_dpdk_menu_optimizations::test_menu_has_advanced_submenu`
dropped `Configure Hugepages` and `Configure IOMMU` from its
required-items list (those are v0.5.34's whole point), and its
regex lookahead anchor changed from `# v0.` (now ambiguous
inside the Advanced block) to `rdma_menu =`.

## [0.5.33] - 2026-06-08

**install_dpdk + install_rdma endpoints escape the netgen-server
cgroup via `systemd-run --wait --pipe --collect`.** v0.5.31's
`APT::Sandbox::User=root` only fixed apt's internal privilege
drop — the netgen-server.service systemd unit's OWN sandbox
(`ProtectSystem=` / `ReadWritePaths=` / `RestrictNamespaces=` /
similar) was still blocking root from writing to
`/var/cache/apt/archives/partial`. Apt was hitting EPERM at the
cgroup level, not the apt-options level.

Full suite: **1,726 passed, 1 skipped** (+10 new tests).

### The bug v0.5.31 missed

v0.5.30's hard gate (post-apt elftools probe) did its job and
v0.5.31's apt sandbox option got rid of the `setgroups EPERM`.
But the operator's next attempt produced a different EPERM:

```
W: chmod 0700 of directory /var/cache/apt/archives/partial
   failed - SetupAPTPartialDirectory (1: Operation not permitted)
E: Failed to fetch http://archive.ubuntu.com/.../python3-pyelftools_0.30-1_all.deb
   Could not open file .../partial/python3-pyelftools_0.30-1_all.deb
   - open (13: Permission denied)
```

`chmod 0700` failing as **root** means it's a cgroup-level deny,
not a Unix-perm deny. The netgen-server.service systemd unit has
some combination of:

* `ProtectSystem=strict` (or `=full`) — makes /var read-only
* `ReadWritePaths=` not including /var/cache/apt
* `RestrictNamespaces=true` — blocks the namespace ops apt's
  fetcher does
* `PrivateMounts=true` — apt's bind-mounts on its cache dir fail

Apt's own options can't fix this — the kernel won't let the
process touch the file regardless of which user it claims to be.

### Fix: escape the cgroup

Same pattern as v0.5.23's wheel-upgrade fix: wrap the script
spawn in `systemd-run` so it runs in a fresh transient unit with
**vanilla defaults** (no inherited sandbox).

```python
cmd = ["bash", script, "--auto"]
if systemd_run:
    cmd = [
        systemd_run,
        "--wait",                            # block until unit exits
        "--pipe",                            # forward stdout/stderr
        "--collect",                         # auto-cleanup unit on exit
        f"--unit=netgen-install-dpdk-runner-{ts}.service",
        "--setenv=HOME=/root",
        "--setenv=AUTO_MODE=1",
        "--setenv=TERM=xterm",
        "--setenv=DEBIAN_FRONTEND=noninteractive",
        "--setenv=DEBIAN_PRIORITY=critical",
        "--",
    ] + cmd
```

The transient unit:
* Inherits **no sandbox** from netgen-server.service (systemd
  starts each transient unit with defaults)
* Has full root access to `/var/cache/apt/` (which is the whole
  point)
* Auto-cleans up on exit (`--collect`) so `systemctl
  list-units` stays tidy
* Auto-blocks netgen-server until install completes (`--wait`)
  so `proc.poll()` tracking, log polling, and the v0.5.30
  hard-gate post-mortem all work unchanged

### Difference vs v0.5.23 (upgrade_wheel)

| | v0.5.23 (upgrade_wheel) | v0.5.33 (install_dpdk + install_rdma) |
|---|---|---|
| Reason for systemd-run | Server restarts mid-install; need pip to survive cgroup-kill | Apt needs to escape sandbox to write /var/cache/apt |
| systemd-run flags | `--no-block --collect` | `--wait --pipe --collect` |
| Tracking | systemctl is-active + ExecMainStatus | proc.poll() (works unchanged with --wait) |
| State persistence | yes (state file across restart) | no (server doesn't restart) |

### Fallback

When `_systemd_run_available()` returns None (non-systemd hosts,
non-root, Docker), endpoints fall back to the bare Popen — same
as v0.5.32. The sandbox bug doesn't apply on those hosts.

### Tests

10 new in `tests/test_v0533_install_endpoints_systemd_run_cgroup_escape.py`:
* api_admin_install_dpdk wraps in `_systemd_run_available()` path
* Wrap uses `--wait` + `--pipe` + `--collect` (all three)
* Unit name is per-invocation timestamped f-string
* All 5 env vars passed via `--setenv` (HOME, AUTO_MODE, TERM,
  DEBIAN_FRONTEND, DEBIAN_PRIORITY)
* Non-systemd hosts fall back to bare `["bash", script, "--auto"]`
* api_admin_install_rdma uses the same systemd-run wrap
* RDMA install uses `--wait` + `--pipe` + `--collect` too
* RDMA unit name distinct from DPDK (eases `systemctl list-units`
  / journalctl grep)
* Endpoint has rationale comment referencing sandbox + v0.5.33

### Retry on srv06 (after wheel upgrade to v0.5.33)

```
Tools → DPDK → Setup DPDK → Make DPDK Ready
```

This time apt will actually be able to download. Step 4 succeeds.
The v0.5.30 hard gate's post-apt probe confirms pyelftools
imports. Step 5 meson setup proceeds. Build runs for 10-30 min
through compile / tx_worker / hugepages / VFIO.

For RDMA, same flow: `Tools → RDMA → Setup RDMA…`. The full
v0.5.28 dep set (libibverbs-dev, librdmacm-dev, libibmad-dev,
libibumad-dev, libibnetdisc-dev, rdma-core, perftest,
rdmacm-utils, ibverbs-utils, infiniband-diags, python3-pyverbs,
opensm, mstflint, libmlx5-dev, libmlx4-dev) installs cleanly.

## [0.5.32] - 2026-06-08

**Make DPDK Ready dialog log viewer is now scrollable + selectable.**
Operator-reported: "can [not] see the full logs due to no scroll
on make dpdk, also copy is not allowed". The `_detail` widget was
a `QLabel` — two UX bugs in one: no scrollbars (long log tails
get cropped) and no text selection/copy (operator can't paste
into bug reports).

Full suite: **1,716 passed, 1 skipped** (+7 new tests, 1 existing
test API-updated for the widget swap).

### Bug

The MakeDpdkReadyDialog rendered status text + the v0.5.20 inline
log tail (30 lines of meson errors, apt failures, etc.) in a
single `QLabel`. QLabel:

* **No scroll.** Renders as a single block at whatever height the
  layout gives it. A 30-line log tail with multi-line meson errors
  and inlined apt log overflowed the label's area → operators saw
  only the top ~10 lines.
* **No text select / copy.** QLabel doesn't allow text-select by
  default. Even `setTextInteractionFlags(Qt.TextSelectableByMouse)`
  only enables click-drag — `Ctrl+C` and right-click-Copy don't
  work because QLabel has no clipboard integration. Operators
  couldn't paste failed logs into chat / bug reports.

### Fix

Swap `QLabel` → `QTextBrowser`:

| Property | QLabel | QTextBrowser |
|---|---|---|
| Scrollbars | none | built-in vertical + horizontal |
| Text selection | requires flag, only click-drag | **on by default in read-only mode** |
| Ctrl+C / right-click-Copy | **broken** | **works** |
| HTML rendering | yes (RichText) | yes |
| `setText(html)` accepted | yes | yes (also `setHtml`) |
| Read-only enforcement | implicit | `setReadOnly(True)` explicit |

Existing `setText(...)` call sites work unchanged — QTextBrowser
accepts HTML through `setText()` just like QLabel's RichText
format.

### Layout

* `setMinimumHeight(180)` — ~12 lines of 11px text visible without
  scroll (covers a typical action description + multi-line status)
* `setMaximumHeight(360)` — caps the area on small screens so the
  dialog doesn't grow unmanageably; operator scrolls within the
  viewer for content past 360px
* `outer.addWidget(self._detail, 1)` — stretch factor 1, expands
  to fill available vertical space when the operator resizes the
  dialog
* `QSizePolicy(Expanding, Expanding)` — explicit expand policy
* `setStyleSheet` adds a light-gray background + border + padding
  so the log area visually distinguishes itself from the
  surrounding controls (was previously just borderless gray text)

### `DpdkBlastFlowDialog` gets the fix for free

`DpdkBlastFlowDialog(MakeDpdkReadyDialog)` inherits `_detail`, so
its log area also becomes scrollable + selectable without
additional changes. One existing test
(`test_blast_flow_stop_response_check_catches_zero_stopped`)
updated from `dlg._detail.text()` (QLabel API) to
`dlg._detail.toPlainText()` (QTextBrowser API).

### `SetupRdmaDialog` was already correct

v0.5.27's SetupRdmaDialog used `QTextEdit` from the start — it
already scrolled and supported text selection. A v0.5.32 test
confirms it wasn't regressed.

### Tests

7 new in `tests/test_v0532_dpdk_dialog_scroll_select.py`:
* `_detail` is `QTextBrowser` or `QTextEdit` (NOT QLabel)
* `_detail.setReadOnly(True)` is set
* `_detail.setMinimumHeight(N)` with N ≥ 100 (enough log lines)
* `_detail` added to layout with stretch ≥ 1
* `QTextBrowser` / `QTextEdit` imported (no NameError on construct)
* `SetupRdmaDialog`'s `log_view` is still a QTextEdit (no
  regression of the already-correct dialog)
* Version pinned at ≥ 0.5.32

1 existing test updated: `test_blast_flow_stop_response_check_catches_zero_stopped`
now reads the widget via `toPlainText()` instead of `text()`.

## [0.5.31] - 2026-06-08

**Real fix: `-o APT::Sandbox::User=root` on every apt invocation.**
Three releases of "install python3-pyelftools" couldn't fix what
v0.5.30's hard gate finally surfaced — apt was failing under
systemd's syscall sandbox before any package could be downloaded.

Full suite: **1,709 passed, 1 skipped** (+4 new tests).

### Root cause finally pinned

The v0.5.30 hard gate ran on srv06 and produced this from
`/tmp/dpdk_deps_install.log`:

```
E: setgroups 65534 failed - setgroups (1: Operation not permitted)
Err:15 ... Could not open file .../python3-pyelftools_0.30-1_all.deb
    - open (13: Permission denied)
W: chown to _apt:root of directory /var/cache/apt/archives/partial
   failed - SetupAPTPartialDirectory (1: Operation not permitted)
[✗] Dependency installation failed
```

By default `apt-get` drops privileges to the unprivileged `_apt`
user for downloads (calls `setgroups()` then `setuid()`). When
the netgen-server systemd unit's syscall filter or
`RestrictSUIDSGID=true` blocks `setgroups()`, apt fails to drop
privs → can't read/write `/var/cache/apt/archives/partial/` → the
download fails silently → the entire install batch errors out.

The script's "Continue anyway?" prompt auto-returned "y" in
AUTO_MODE (v0.5.29 fix didn't catch this fall-through) → Step 5
meson errored on the missing pyelftools.

### Fix: `-o APT::Sandbox::User=root` on every apt call

```bash
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    -o APT::Sandbox::User=root \           # ← v0.5.31
    --option Acquire::http::Timeout=30 \
    ...
```

Tells apt to skip the privilege drop entirely. Safe because
netgen-server already runs as root. Sidesteps the systemd
sandbox cleanly without needing to modify the systemd unit.

Applied to:
* `deps_install_cmd` in install_dpdk.sh (the main install)
* `apt-get update -y` retry loop in install_dpdk.sh step 4
* `core_apt_cmd` in install_rdma.sh
* `mlx5_apt_cmd` in install_rdma.sh
* `apt-get update` at top of install_rdma.sh

### Why this took 3 releases to find

* **v0.5.25** added `python3-pyelftools` to the apt batch
* **v0.5.26** added 8 more optional packages
* **v0.5.29** dropped the early-return that was skipping apt-install
  entirely (`check_dpdk_dependencies` only verified 7 of 16+ packages)
* **v0.5.30** added the hard-gate that finally PRESERVED the
  apt log + inlined it into the failure dialog

Without v0.5.30, the apt log got `rm -f`'d before the operator
could read it. With v0.5.30's hard-gate diagnostic, the actual
`setgroups EPERM` error became visible. v0.5.31 fixes the root
cause.

### Retry on srv06 (after wheel upgrade to v0.5.31)

```
Tools → DPDK → Setup DPDK → Make DPDK Ready
```

Step 4 will now succeed. Apt downloads complete. pyelftools
installs. Step 5 meson setup proceeds without the missing-module
error.

### Tests

4 new in `tests/test_v0531_apt_sandbox_user_root.py`:
* Every apt-get invocation in install_dpdk.sh includes
  `APT::Sandbox::User=root` (multi-line continuations covered)
* Every apt-get invocation in install_rdma.sh includes it too
* Option documented with rationale (must mention `setgroups`
  or `RestrictSUIDSGID` or `systemd` near the use site so a
  future refactor doesn't silently drop it)
* Version pinned at ≥ 0.5.31

The test helper deliberately skips lines where `apt-get` appears
inside `log_error`/`echo`/`printf` strings — those are recovery
text for operators, not executed commands.

## [0.5.30] - 2026-06-08

**Hard gate on `python3-pyelftools` after Step 4 apt install.**
Even with v0.5.29's always-run apt-install, the script's
"Continue anyway?" prompt on apt-failure silently swallowed
failures in AUTO_MODE → Step 5 meson still died with the same
"missing python module: elftools" error. Operator saw the
meson error but had no diagnostic for what apt actually did.

v0.5.30 closes the loop: post-apt probe with `python3 -c "import
elftools"`. If still missing, **exit Step 4 with a precise
error + last 30 lines of the apt install log inlined into the
install_dpdk log**. The GUI's "last 30 log lines" view now
surfaces the apt failure directly instead of the downstream
meson stack-trace.

Full suite: **1,705 passed, 1 skipped** (+7 new tests).

### What was broken in v0.5.29

```bash
# Pre-v0.5.30 Step 4 tail:
if (umask 077 && eval "$deps_install_cmd" | tee /tmp/dpdk_deps_install.log); then
    log_success "Dependencies installed successfully"
else
    ...
    log_error "Dependency installation failed"
    if [[ $(prompt_yes_no "Continue anyway?") != "y" ]]; then
        exit 1
    fi
fi
rm -f /tmp/dpdk_deps_install.log   # destroys the evidence
```

In AUTO_MODE (GUI / install_dpdk endpoint), `prompt_yes_no`
returns "y" automatically. So an apt failure → `log_error`
→ silent continuation → Step 5 → meson error. The apt log
gets `rm -f`'d before the operator can see what failed.

### v0.5.30 hard gate

```bash
if ! python3 -c "import elftools" 2>/dev/null; then
    log_error "════════════════════════════════════════════════════════════"
    log_error "  CRITICAL: python3-pyelftools is NOT installed."
    log_error "  DPDK 23.11 meson setup hard-requires the elftools module."
    log_error "  See /tmp/dpdk_deps_install.log for the apt install log."
    log_error "  Manual recovery:"
    log_error "    apt-get update && apt-get install -y python3-pyelftools"
    log_error "════════════════════════════════════════════════════════════"
    if [[ -r /tmp/dpdk_deps_install.log ]]; then
        log_error "Last 30 lines of apt install log:"
        tail -30 /tmp/dpdk_deps_install.log | while IFS= read -r line; do
            log_error "  | $line"
        done
    fi
    exit 1   # /tmp/dpdk_deps_install.log preserved for debugging
fi
log_success "python3-pyelftools verified (elftools module importable)"
```

### Failure-mode transition

| Stage | Before v0.5.30 | After v0.5.30 |
|---|---|---|
| apt install fails on pyelftools | `log_error "Dependency installation failed"` → continue | Same |
| Step 4 ends | proceeds to Step 5 with broken state | **HARD GATE: exit 1 with diagnostic** |
| Step 5 meson | dies with `missing python module: elftools` | doesn't run |
| Operator sees | confusing meson error 100+ lines later | **Step 4 elftools error + apt log tail right there** |
| Apt install log | `rm -f`'d before operator can read | **preserved** at `/tmp/dpdk_deps_install.log` |
| GUI "last 30 log lines" | meson stack-trace (unactionable) | **apt failure + recovery command** (actionable) |

### Manual recovery (operator paste-able)

The error message includes the literal apt-install command so an
operator hit by this can:

```bash
ssh root@<server> 'apt-get update && apt-get install -y python3-pyelftools'
```

Then retry Make DPDK Ready. The script is idempotent — picks up
at Step 4, sees pyelftools present, log_success "verified",
proceeds to Step 5.

### Diagnostic commands for current srv06 state

```bash
ssh root@san-hp-srv06 'bash -s' <<'EOF'
echo "Is install_dpdk.sh v0.5.30?"
grep -c "v0.5.30\|HARD GATE" /opt/netgen/resources/dpdk/install_dpdk.sh

echo "Is pyelftools installed?"
dpkg -l python3-pyelftools | tail -3
python3 -c "import elftools; print(elftools.__file__)" 2>&1

echo "Was apt-get install attempted in last run?"
tail -80 /tmp/netgen_install_dpdk_*.log | grep -E "Updating|Installing|elftools|deps_install"
EOF
```

### Tests

7 new in `tests/test_v0530_install_dpdk_hard_gate_pyelftools.py`:
* Post-apt `python3 -c "import elftools"` probe exists
* Probe is in negated condition (`if !`) so failure path triggers
* Probe followed by `exit 1` (not silent continuation)
* Hard-gate failure branch does NOT `rm -f` the apt install log
* Hard-gate failure branch `tail`s the apt log + inlines lines
  through the script's own log channel (so GUI's last-30-lines
  view surfaces them)
* Failure message includes literal manual recovery command
  (`apt-get install -y python3-pyelftools`)
* Success path emits `log_success` with elftools / pyelftools in
  the message (positive confirmation in the log)

## [0.5.29] - 2026-06-08

**install_dpdk.sh always runs `apt install` — early-return on stale
7-package check removed.** Same `missing python module: elftools`
recurred on srv06 in v0.5.28 because the script SKIPPED the apt
step when its stale check passed — `python3-pyelftools` (v0.5.25
add) + 8 v0.5.26 optional packages never installed on hosts that
had run Setup DPDK in v0.5.13 or earlier.

Full suite: **1,698 passed, 1 skipped** (+7 new tests).

### Operator-reported failure (srv06, post v0.5.28 wheel upgrade)

```
Step 5: Building DPDK
DPDK build directory exists, wiping and reconfiguring...
buildtools/meson.build:58:8: ERROR: Problem encountered:
    missing python module: elftools
[x] DPDK meson setup failed
```

Same error v0.5.25 was supposed to fix. Why did it recur?

### Root cause: stale check gate

`step_install_dependencies()` started with:

```bash
if check_dpdk_dependencies; then
    log_success "All DPDK dependencies are already installed"
    return 0
fi
```

`check_dpdk_dependencies` verified **only 7 packages** —
`meson`, `ninja`, `pkg-config`, `gcc`, `libnuma-dev`, `libelf-dev`,
`libpcap-dev`. The actual apt batch installs **16+ packages**
including v0.5.25's `python3-pyelftools` and v0.5.26's 8
optionals.

An operator who'd run Make DPDK Ready in v0.5.13 (when only the
7 packages mattered) had the check PASS on every subsequent
v0.5.28 retry → apt-install SKIPPED → python3-pyelftools never
installed → Step 5 meson errored.

The check function was lying — saying "all installed" while the
actual installable set had grown.

### Fix: always run `apt install`

Dropped the early-return. `step_install_dependencies` now always
calls `eval "$deps_install_cmd"`, regardless of check result.
`apt-get install` is idempotent for already-installed packages
(logs "X is already the newest version" and moves on) — costs
~5-10s on subsequent runs. Operator-trust invariant: **Make
DPDK Ready installs every dep the script knows about, every
time.**

`check_dpdk_dependencies` stays — its diagnostic logging is
useful — but no longer GATES apt. Augmented to also probe
`python3 -c "import elftools"` (module-name probe, not
distro-specific package-name probe — works across apt-installed,
pip-installed, and non-Debian distros).

### Auto-mode gate added

Pre-fix, the early-return masked another bug: the `prompt_yes_no
"Install missing dependencies?"` prompt would fire even in
AUTO_MODE (which has no TTY) and hang the install_dpdk endpoint
indefinitely. With the early-return gone, the prompt would fire
unconditionally on every retry — so wrapped it in `[[
"$AUTO_MODE" != "1" ]]` so the GUI / endpoint path runs the
apt install silently.

### Retry on srv06

```
Tools → DPDK → Setup DPDK → Make DPDK Ready
```

Script's idempotent. On the next run apt-install runs (Step 4),
`python3-pyelftools` lands (along with libssl-dev, libjansson-dev,
libbpf-dev, libxdp-dev, libbsd-dev, zlib1g-dev, libfdt-dev,
libarchive-dev — anything missing from earlier runs), meson
setup at Step 5 succeeds, build proceeds.

### Tests

7 new in `tests/test_v0529_install_dpdk_always_apt_install.py`:
* Early-return on `check_dpdk_dependencies` success is GONE
* `check_dpdk_dependencies` called as diagnostic only (with
  `|| true`)
* `prompt_yes_no` gated on `AUTO_MODE != 1` (no TTY hang)
* `deps_install_cmd` invoked unconditionally
* `check_dpdk_dependencies` probes `python3 -c "import elftools"`
* Probe uses the importable MODULE name, not the distro-specific
  package name (portable across apt / pip / non-Debian)
* Version pinned at ≥ 0.5.29

## [0.5.28] - 2026-06-07

**Comprehensive RDMA dep coverage in `install_rdma.sh`.** Operator
request right after v0.5.27 shipped: "make sure all the
dependencies for rdma should be taken care during Setup RDMA".
The v0.5.27 minimum-viable list missed several packages
operators routinely need.

Full suite: **1,691 passed, 1 skipped** (+8 new tests).

### What v0.5.27 missed

| Category | Missing | Why it matters |
|---|---|---|
| Userspace libs | `librdmacm-dev`, `libibmad-dev`, `libibumad-dev`, `libibnetdisc-dev` | RDMA-CM headers (needed to compile any RDMA-CM-using code) + MAD/fabric-discovery libs (needed by ibdiagnet, ibnetdiscover) |
| Test tools | `rdmacm-utils` | rping / ucmatose / ucmd — RDMA-CM smoke tests, the operator's first "does my stack work" probe |
| Python | `python3-pyverbs` | Python ibv_* bindings — used by diagnostic scripts and lets ops script probes without writing C |
| Subnet manager | `opensm` | Required on native InfiniBand fabrics without a switch-resident SM. Installed but **disabled by default** — operator must explicitly `systemctl enable --now opensm` |
| Firmware tools | `mstflint` | Mellanox firmware management (mstflint, mstconfig, mstfwreset) — query NIC FW version, change port mode (Ethernet ↔ InfiniBand), apply updates |
| Older Mellanox | `libmlx4-dev` | ConnectX-3 / ConnectX-2 dev headers (still common in lab gear from 2014-2018). Pre-v0.5.28 mlx batch only had libmlx5-dev (ConnectX-4+) |
| Kernel modules | `rdma_ucm`, `iw_cm` | `rdma_ucm` is the userspace bridge to `rdma_cm` — without it, librdmacm calls fail with EBADF on `/dev/infiniband/rdma_cm`. `iw_cm` for iWARP CM. |

### `rdma_ucm` is the silent killer

Pre-v0.5.28, install_rdma.sh loaded `ib_uverbs`, `rdma_cm`,
`ib_umad` — but not `rdma_ucm`. That subtle absence meant
`perftest`'s `ib_send_bw` (which uses RDMA-CM for connection
establishment by default) would fail with cryptic EBADF errors
on `/dev/infiniband/rdma_cm`. Operators would assume their NIC
was broken when the actual issue was a missing kernel module.

v0.5.28's expanded `rdma_modules` array fixes it: `("ib_uverbs"
"rdma_cm" "rdma_ucm" "ib_umad" "iw_cm")`. The persisted
`/etc/modules-load.d/netgen-rdma.conf` carries the same list so
reboots don't regress.

### `opensm` disabled-by-default safety

OpenSM can take over fabric management on InfiniBand networks.
On RoCE-only hosts that's harmless waste; on a fabric with a
switch-resident SM or another OpenSM instance, having two SMs
fight is destructive (state thrash, ARP storms, dropped
connections).

The script explicitly `systemctl disable --now opensm` after
install. Operators with native IB fabrics that need OpenSM
enable it manually. The disable is gated on the service
existing (so non-opensm hosts don't see scary errors).

### Wizard intro updated

The Setup RDMA dialog's intro text now enumerates the full set
grouped by category — operators can see at a glance what they're
getting. Test pins references to the marquee additions so the
dialog text can't silently drift from the script.

### Tests

8 new in `tests/test_v0528_install_rdma_full_coverage.py`:
* All 13 core packages present in `core_apt_cmd`
* All 2 Mellanox packages (`libmlx5-dev` + `libmlx4-dev`)
  present in `mlx5_apt_cmd`
* All 5 kernel modules (`ib_uverbs`, `rdma_cm`, `rdma_ucm`,
  `ib_umad`, `iw_cm`) in `rdma_modules`
* `opensm.service` disabled after install + gated on service
  existing (avoids errors on hosts without opensm)
* Wizard intro mentions `librdmacm-dev`, `rdmacm-utils`,
  `python3-pyverbs`, `opensm`, `mstflint`, `libmlx4-dev`,
  `rdma_ucm`, `iw_cm`
* v0.5.27 core/mlx split invariant preserved (anti-collapse)
* v0.5.27 Mellanox-fail-tolerant invariant preserved (anti-
  regression on double-package mlx batch)

## [0.5.27] - 2026-06-07

**RDMA install split from DPDK install** — operator-requested
separation. The RDMA stack (libibverbs-dev, rdma-core, perftest,
ibverbs-utils, infiniband-diags + optional libmlx5-dev) is now
its own wizard, not a side-effect of "Make DPDK Ready".

Full suite: **1,683 passed, 1 skipped** (+17 new, 4 existing
tests updated for the split).

### Operator request

> rdma install should be separate, it should not be part of dpdk install

### Rationale for separation

* **DPDK runs on any NIC** — Intel, Broadcom, virtio. Pre-v0.5.27
  install_dpdk.sh dragged ~30 MB of RDMA stack onto Intel/Broadcom
  hosts that didn't need it.
* **RDMA testing is independent** — netgen's perftest orchestrator
  (Tools → RDMA → Blast a RDMA Flow / Topology Test) just needs
  libibverbs + perftest. Operators who only want RDMA tests
  shouldn't have to run the 10-step DPDK build.
* **Composable** — operators wanting DPDK on Mellanox NICs run
  Setup RDMA first (gets libibverbs), then Setup DPDK (the mlx5
  PMD picks up libibverbs at meson configure time). install_dpdk.sh
  detects Mellanox NICs without libibverbs and prints a clear
  pointer at Setup RDMA so the order isn't ambiguous.

### What moved

| Package | Before v0.5.27 | After v0.5.27 |
|---|---|---|
| `libibverbs-dev` | install_dpdk.sh core | install_rdma.sh core |
| `rdma-core` | install_dpdk.sh core | install_rdma.sh core |
| `perftest` | install_dpdk.sh core | install_rdma.sh core |
| `libmlx5-dev` | install_dpdk.sh mlx5 batch (fail-tolerant) | install_rdma.sh mlx5 batch (fail-tolerant) |
| `ibverbs-utils` | not installed | install_rdma.sh core (new) |
| `infiniband-diags` | not installed | install_rdma.sh core (new) |

`ibverbs-utils` (ibv_devices, ibv_devinfo, ibv_rc_pingpong) and
`infiniband-diags` (ibstat, ibportstate) were missing pre-v0.5.27.
Diagnostic blind spot — operators couldn't `ibv_devices` to confirm
their RDMA stack was working.

### New artifacts

* **`resources/dpdk/install_rdma.sh`** — 220 LOC, 4 steps:
  1. apt-install RDMA stack (core batch + Mellanox-optional batch)
  2. modprobe ib_uverbs / rdma_cm / ib_umad + persist to
     `/etc/modules-load.d/netgen-rdma.conf`
  3. systemctl enable rdma-hw.target / rdma.service (whichever ships)
  4. verify via `ibv_devices` + count detected RDMA HCAs
* **`POST /api/admin/install_rdma`** — mirrors the install_dpdk
  endpoint pattern (background Popen, distinct
  `_ADMIN_INSTALL_RDMA_STATE`, defense-in-depth HOME setdefault per
  v0.5.21 lesson)
* **`GET /api/admin/install_rdma/log`** — simpler shape than
  `/install_dpdk/log` (no multi-phase parsing; install_rdma is
  short)
* **`widgets/setup_rdma_dialog.py`** — focused 220 LOC wizard
  (intro panel → Install button → live log view → status banner).
  No IOMMU reboot prompt, no NIC bind step — RDMA doesn't need
  either.
* **Tools → RDMA → Setup RDMA…** — pinned at the top of the RDMA
  submenu, before "Blast a RDMA Flow..." (mirrors Setup DPDK as
  the entry-point of the DPDK submenu)

### install_dpdk.sh slim-down

The Mellanox NIC sanity check is the only new addition. If
`lspci | grep mellanox` finds Mellanox hardware AND
`ldconfig -p | grep libibverbs` returns nothing, install_dpdk.sh
logs a warning pointing operators at Setup RDMA / install_rdma.sh.
Otherwise it stays out of their way — Intel/Broadcom hosts don't
see any RDMA references at all.

### Tests

17 new in `tests/test_v0527_rdma_install_split.py`:
* install_rdma.sh exists + wheel package-data still globs `*.sh`
* Core RDMA stack pkgs present (libibverbs-dev, rdma-core,
  perftest, ibverbs-utils, infiniband-diags)
* Mellanox `libmlx5-dev` kept in separate fail-tolerant batch
* Kernel modules (ib_uverbs / rdma_cm / ib_umad) loaded +
  persisted via /etc/modules-load.d/
* End-of-script `ibv_devices` verify
* Strict-mode (`set -euo pipefail`) + HOME-unbound defense
* install_dpdk.sh no longer references libibverbs-dev /
  rdma-core / perftest / libmlx5-dev / mlx5_install_cmd
* install_dpdk.sh warns when Mellanox NIC detected without
  libibverbs, pointing operators at Setup RDMA
* `/api/admin/install_rdma` + `/log` endpoints exist
* `_ADMIN_INSTALL_RDMA_STATE` distinct from
  `_ADMIN_INSTALL_STATE` (no clobbering)
* `HOME` env set in install_rdma Popen
* `SetupRdmaDialog` exists + drives the install_rdma endpoints
* Menu item "Setup RDMA…" wired in main.py + appears BEFORE
  "Blast a RDMA Flow..." in the submenu
* `show_setup_rdma_dialog` handler in rdma_menu_actions.py +
  instantiates SetupRdmaDialog

4 pre-existing tests updated to reflect the v0.5.27 split:
* `test_v0525_install_dpdk_pyelftools::test_pyelftools_in_core_batch_not_mlx5_batch` — now confirms mlx5_install_cmd is GONE
* `test_v0525_install_dpdk_pyelftools::test_apt_install_preserves_core_dpdk_deps` — required list trimmed to DPDK-only
* `test_v0526_install_dpdk_full_deps::DRIVER_AND_LIB` — trimmed
* `test_v0526_install_dpdk_full_deps::test_optional_deps_in_core_batch_not_mlx5` — now confirms mlx5_install_cmd is GONE
* `test_rdma_install_split::*` — re-purposed from "split into 2 batches in install_dpdk.sh" to "split into separate install_rdma.sh"

### Operator workflow recap

| Goal | Steps |
|---|---|
| DPDK only (Intel/Broadcom NICs) | Tools → DPDK → Setup DPDK |
| RDMA tests only (no DPDK) | Tools → RDMA → Setup RDMA |
| DPDK on Mellanox NICs | Tools → RDMA → Setup RDMA (FIRST), then Tools → DPDK → Setup DPDK |
| Both, sequentially | Setup RDMA → Setup DPDK (any order works; Mellanox PMD only links if libibverbs is present at DPDK build time) |

## [0.5.26] - 2026-06-07

**Comprehensive DPDK 23.11 dep audit — install_dpdk.sh now apt-
installs the full transitive set for `-Dexamples=all` + default-
enabled telemetry.** Eliminates the drip-feed of "missing
package X" → ship → "missing package Y" cycle the v0.5.25
pyelftools fix would have started.

Full suite: **1,666 passed, 1 skipped** (+7 new tests).

### Operator request

> check full what other dependency is missing for dpdk

After v0.5.25 unblocked DPDK meson setup with python3-pyelftools,
audit the full dep surface against DPDK 23.11's actual
requirements (mandatory + optional driver/feature deps).

### Audit categories

**Mandatory** (already had all of these by v0.5.25 — pinning for
regression):

| Package | Enables |
|---|---|
| `build-essential` | C11 compiler toolchain |
| `meson` | Build system |
| `ninja-build` | Build executor |
| `pkg-config` | Library discovery |
| `libnuma-dev` | EAL hard-requires NUMA topology |
| `python3-pyelftools` | `buildtools/check-symbols.sh` ELF parsing |
| `${kernel_headers}` | kmod / kni build |

**Driver/library** (already had):

| Package | Enables |
|---|---|
| `libelf-dev` | BPF library |
| `libpcap-dev` | pcap PMD + pdump |
| `libibverbs-dev` | Mellanox PMDs (ibverbs interface) |
| `rdma-core` | Mellanox PMDs (full RDMA stack) |
| `perftest` | netgen's RDMA orchestrator (ib_send_bw etc.) |

**Optional — ADDED in v0.5.26** (needed by `-Dexamples=all` +
default-enabled telemetry):

| Package | Enables |
|---|---|
| `libssl-dev` | crypto PMDs + crypto examples (l2fwd-crypto, ipsec-secgw) |
| `libjansson-dev` | telemetry JSON encoding (default-enabled in 23.11) |
| `libbpf-dev` | BPF library + AF_XDP PMD |
| `libxdp-dev` | AF_XDP PMD (paired with libbpf) |
| `libbsd-dev` | BSD-isms (strlcpy etc.) used by some examples |
| `zlib1g-dev` | compression PMDs (compress/zlib) |
| `libfdt-dev` | Flattened Device Tree config (ARM-ish; harmless on x86) |
| `libarchive-dev` | resource-pack tests + some examples |

### Cost / benefit

* **Apt download/install**: ~20-40 MB extra (one-time on Make
  DPDK Ready).
* **Build time impact**: negligible — meson silently no-ops
  features whose corresponding deps aren't requested even when
  installed; only the specific examples or PMDs needing each
  dep change behavior.
* **Benefit**: future feature requests (AF_XDP, telemetry
  JSON export, crypto PMDs) don't require a new release cycle —
  the deps are already there.

### Rationale comment in install_dpdk.sh

The dep list now has a multi-line comment block explaining what
each package enables. A future refactor that wants to slim the
list will see *why* each dep is there before deleting it. Test
`test_dep_comment_explains_optional_rationale` enforces that
the comment references the major feature areas (telemetry,
AF_XDP, crypto, examples) so the explanation can't silently rot.

### Retry on srv06

```
Tools → DPDK → Setup DPDK → Make DPDK Ready
```

Script's idempotent. The apt step now installs the full set;
meson re-runs in `/opt/dpdk-build`; previous `meson-logs/` get
overwritten with the new (successful) setup.

### Tests

7 new in `tests/test_v0526_install_dpdk_full_deps.py`:
* All mandatory packages present in `deps_install_cmd`
* All driver/lib packages present
* All v0.5.26 audit packages present
* Optional deps in CORE batch (not mlx5 batch — mlx5 batch can
  fail independently on non-MOFED hosts)
* No duplicate packages in apt list (catches refactor lapse)
* Dep-catalog comment references telemetry / AF_XDP / crypto /
  examples (rationale anti-rot)
* Version pinned at ≥ 0.5.26

## [0.5.25] - 2026-06-07

**install_dpdk.sh apt-installs `python3-pyelftools`.** DPDK 23.11
meson setup fails with "missing python module: elftools" without
it. Reported on srv06 (Ubuntu 24.04) — Step 5 (Building DPDK)
errored out at `buildtools/meson.build:58:8`.

Full suite: **1,659 passed, 1 skipped** (+4 new tests).

### Operator-reported failure

```
Configuring DPDK build (disabling: net/mana)...
Program python3 found: YES (/usr/bin/python3)
buildtools/meson.build:58:8: ERROR: Problem encountered: missing python module: elftools
A full log can be found at /opt/dpdk-build/build/meson-logs/meson-log.txt
[x] DPDK meson setup failed
```

### Fix

One-line addition to `deps_install_cmd` in
`resources/dpdk/install_dpdk.sh` — `python3-pyelftools` joins the
existing batch (`build-essential meson ninja-build pkg-config
libnuma-dev libelf-dev libpcap-dev libibverbs-dev rdma-core
perftest ${kernel_headers}`).

### Why pyelftools

DPDK 23.11's build system uses `pyelftools` for:
- `buildtools/check-symbols.sh` — ABI-compatibility symbol checks
- `buildtools/options-ibverbs-static.sh` — ibverbs static linkage helpers

Both invoke Python with `from elftools.elf.elffile import ELFFile`
to parse compiled binaries. Missing → meson setup hard-fails
before any compilation runs.

### Why pyelftools landed in the core batch, not the mlx5 batch

The mlx5 batch (`libmlx5-dev`) was split out in an earlier
release because hosts without the Mellanox MOFED apt repo
(svl-d-ai-srv04, etc.) fail it with rc=100 → that would have
poisoned the rest of the install if it were in one batch.
pyelftools is not Mellanox-specific and is required by EVERY
DPDK 23.11 build, so it stays in the always-required core
batch.

### Retry on srv06

```
Tools → DPDK → Setup DPDK → Make DPDK Ready
```

The wizard re-invokes `install_dpdk.sh`. The script's idempotent
— it cd's into the existing `/opt/dpdk-build` and reruns
`meson setup`. With `python3-pyelftools` installed this time,
Step 5 will succeed and the build will proceed through
compile / tx_worker / hugepages / vfio.

### Tests

4 new in `tests/test_v0525_install_dpdk_pyelftools.py`:
- `deps_install_cmd` includes `python3-pyelftools`
- It's in the core batch, NOT the optional mlx5 batch
- The other 10 core deps weren't dropped by the refactor
- Version pinned at ≥ 0.5.25

## [0.5.24] - 2026-06-07

**Client treats `rc=None + log_path=null` as "server lost state,
check `/api/health`" instead of "pip failed".** Necessary for any
operator upgrading FROM a pre-v0.5.23 server — those servers
don't have the upgrade-state persistence v0.5.23 added, so a
mid-upgrade restart wipes their in-memory state. The next
`/log` poll returns `{running: false, log_path: null,
return_code: null}` and the client (pre-v0.5.24) aborted with
"pip exited rc=None" — even when the upgrade had actually
succeeded.

Full suite: **1,655 passed, 1 skipped** (+13 new tests).

### Operator-reported failure (srv06, second attempt v0.5.21 → v0.5.23)

```
Successfully installed Flask-3.1.3 ... ostg-trafficgen-0.5.23 ...
[INFO] $ /opt/netgen-server/netgen-venv/bin/python -c import flask, ...
[client] pip exited rc=None; aborting
```

The visible `Successfully installed ... ostg-trafficgen-0.5.23`
line proves the install succeeded. The `[INFO] $` line proves
the netgen-upgrade post-install import check kicked off. So
where did the `rc=None` come from?

The v0.5.21 server (no state persistence — that's v0.5.23+) got
restarted between the import check starting and the client's
next poll. The post-restart server has empty
`_ADMIN_UPGRADE_STATE`, so `/api/admin/upgrade_wheel/log` returns
`{running: false, log_path: null, return_code: null}`. The
client conflated `rc=None` (no recorded exit code) with
`rc=N` (explicit pip failure) and aborted, never reaching the
`/api/health` stage that would have proved the upgrade actually
succeeded.

### Fix: differentiate by `log_path` presence

| `rc` | `log_path` | Meaning | New behavior |
|---|---|---|---|
| `0` | any | pip succeeded | Stage 3: poll `/api/health` |
| `N` (int) | any | pip explicitly failed | Abort, log `rc=N` |
| `None` | set | proc died mid-flight (signal-kill) | Abort, log `rc=None` |
| `None` | `null` | **server forgot — restart wiped state** | **Stage 3: poll `/api/health` with version-verify** |

### Verification adds version-check

The `/api/health` stage now parses the EXPECTED version from the
uploaded wheel filename (PEP 427) and compares against the
running server's reported version. Catches three new failure
modes the old "any 200 OK = success" check missed:

* Server restarted onto an OLDER cached install
* `/api/health` is up but the upgrade silently no-op'd
* Wrong wheel installed (e.g. operator picked a stale wheel)

Match → declare success ("server at v0.5.24"). Mismatch after
the 90s deadline → declare failure with both versions
("server still on v0.5.21").

Tolerates very old servers whose `/api/health` doesn't expose a
`netgen_version` field — falls back to "any 200 OK = success"
so we don't false-fail on hosts predating the version field.

Wheel filenames that don't parse (operator picked an odd
filename) → legacy "any 200 OK = success" path. PEP 427
parser handles canonical names AND optional build tags.

### Why v0.5.23 alone isn't enough

v0.5.23's server-side state persistence helps when the SOURCE
server is v0.5.23+. But every operator currently on v0.5.6 →
v0.5.22 upgrades through the OLD code path one last time — and
that server doesn't have the persistence. The CLIENT has to
handle "server forgot" gracefully so this transition isn't
booby-trapped.

### Recovery semantics

This is purely a client improvement. No server changes; no
state migration. After the wheel containing v0.5.24 is
installed (via the same client GUI flow, even from a
pre-v0.5.24 client), future upgrades from that server are
clean. The fix is in the SHIPPED client binary
(Netgen-Client-0.5.24-*) too, so once an operator updates
their desktop client, ALL their pre-v0.5.23 server upgrades
become resilient.

### Tests

13 new in `tests/test_v0524_client_health_arbitrates_rc_none.py`:

* `rc=None` branched separately from `rc=N` (int)
* Branch checks `log_path` to distinguish lost-state from
  signal-kill
* Lost-state branch sets `restart_seen=True` + `break`s (does
  NOT abort)
* Lost-state branch logs a diagnostic mentioning `/api/health`
  (operator sees the recovery path, not just silence)
* Genuine signal-kill (`rc=None` + `log_path=set`) still aborts
* `_parse_wheel_version` exists + parses canonical wheels
  (`ostg_trafficgen-0.5.23-py3-none-any.whl` → `0.5.23`)
* Parser handles PEP 427 optional build tags
  (`name-0.5.24-1-py3-...` → `0.5.24`)
* Parser returns `None` on garbage filenames
* `/api/health` stage uses `_parse_wheel_version`
* Server version compared against expected
* Missing version field tolerated (very old servers → "any
  200 OK = success")
* Failure message includes `last_seen_version` + `expected`
  on mismatch (operator can act)

## [0.5.23] - 2026-06-07

**Wheel-upgrade survives the cgroup-kill death spiral.** Operator
reported on srv06: pip uninstalled `ostg-trafficgen-0.5.13`, then
"Connection reset by peer" and `pip exited rc=None; aborting`.
Wheel never installed; site-packages half-uninstalled; recovery
required SSH.

Full suite: **1,642 passed, 1 skipped** (+18 new tests).

### What happened

Pip ran as a child of netgen-server's flask process, inside
`netgen-server.service`'s cgroup. Pip uninstalled the
ostg-trafficgen package cleanly. Some other flask worker (stats
poll, healthcheck, anything) tripped an ImportError on the now-
deleted code; flask crashed; systemd reaped the cgroup;
**systemd killed pip too**, because cgroup-kill cascades to
every PID in the cgroup regardless of process-group or session.

The bug isn't pip. The bug isn't flask either. The bug is the
shape — pip ran in the same cgroup as the service it was
upgrading. `setsid` / `nohup` / `start_new_session=True` don't
help: process group ≠ cgroup. systemd kills by cgroup.

### Fix

Wrap the pip spawn in a `systemd-run` transient unit:

```python
systemd_unit = f"netgen-upgrade-runner-{int(time.time())}.service"
cmd = [
    "systemd-run", "--no-block", "--collect",
    f"--unit={systemd_unit}",
    "--setenv=HOME=/root",
    f"--property=StandardOutput=append:{log_path}",
    f"--property=StandardError=append:{log_path}",
    "--",
] + cmd  # the original pip / netgen-upgrade invocation
```

Pip now lives in `netgen-upgrade-runner-*.service` (its own
cgroup). Whatever happens to `netgen-server.service` no longer
affects pip. The netgen-upgrade script itself triggers the
post-install `systemctl restart netgen-server` — that cleanly
cycles the server while the detached cgroup keeps pip alive.

### Status tracking switch — `proc.poll()` → `systemctl`

With `--no-block`, systemd-run's dispatcher exits in milliseconds
(rc=0 = "unit queued"). The local Popen handle no longer reflects
pip's actual lifecycle. The log endpoint now checks `systemctl
is-active <unit>` + `systemctl show <unit>
--property=ExecMainStatus` when `systemd_unit` is set, falling
back to `proc.poll()` for the legacy code path (non-systemd
hosts, non-root, Docker).

The server-side "schedule restart on pip success" trigger is
**skipped** when `systemd_unit` is set — the detached
netgen-upgrade script handles its own restart; a double-restart
would race with the in-flight script.

### State persistence — `/var/lib/netgen-server/upgrade-state.json`

The restart triggered by netgen-upgrade kills the in-memory
`_ADMIN_UPGRADE_STATE` (it lived in the now-dead server process).
The post-restart server starts with empty state, so the next
`/api/admin/upgrade_wheel/log` poll would return `running: false,
log_path: null, return_code: null` — and the client logs `pip
exited rc=None; aborting` on a SUCCESSFUL upgrade.

`_admin_upgrade_persist()` writes a snapshot (minus the
non-pickleable Popen object) atomically (`tmp + os.replace`) on
every transition. `_admin_upgrade_load()` runs at module import
so the post-restart server picks up where the dead one left off.
Both exception-swallowing — a corrupt state file must NOT brick
server startup.

### Recovery path for operators still on v0.5.22 with the bug

```bash
ssh root@<server> 'bash -s' <<'EOF'
set -e
WHEEL=$(ls -t /tmp/netgen_upgrade/*.whl | head -1)
/opt/netgen-server/netgen-venv/bin/pip install \
    --force-reinstall --no-cache-dir "$WHEEL"
systemctl restart netgen-server
EOF
```

Once recovered, v0.5.23+ wheel upgrades won't repeat the failure.

### Tests

18 new in `tests/test_v0523_upgrade_detached_cgroup.py` pin:
detection cached + euid-gated; systemd-run wrap with --no-block
+ --collect + unit pattern; StandardOutput/Error redirect to log
file; HOME set; +detached mode tag; systemd_unit in state +
response; log endpoint branches on systemd_unit; `_systemd_unit_state`
helper uses is-active + ExecMainStatus; server-side restart
gated on `not systemd_unit`; restart_scheduled flipped True in
detached mode; state file at /var/lib/netgen-server/; atomic
write; snapshot excludes Popen; load called at module scope;
loader tolerates missing/corrupt file; legacy proc.poll() path
preserved.

### Notes

* **Why not change KillMode on the systemd unit?** Would require
  reinstalling the unit on every upgrade — fragile state machine
  on a hot path. systemd-run is stateless from netgen-server's
  perspective.
* **Why not setsid + double-fork?** Process group ≠ cgroup.
  systemd kills by cgroup. We tested this in v0.4.x — didn't help.
* **systemd-run absent / non-root**: helper returns None,
  endpoint falls back to legacy path. Bug recurs on those hosts,
  but they're a minority and the legacy path is what shipped
  pre-v0.5.23.

## [0.5.22] - 2026-06-07

**CI optimization: tarball workflow no longer auto-triggers on
tags.** Per-release rebuild was burning ~5 min of CI on a 196 MB
artifact most releases didn't change.

Full suite: 1,624 passed, 1 skipped (+6 new tests, 1 existing test
inverted).

### Operator request

> do not generate tar.gz at every release

### Rationale

* Tarball is ~196 MB; per-tag build cost ~5 min of CI (apt +
  python-build-standalone download + venv build + pack)
* Tarball is only consumed by **fresh installs**. Existing installs
  upgrade via wheel through the GUI — they never touch the tarball.
* The v0.5.6 → v0.5.21 cascade today touched code bundled in the
  wheel (run_tgen_server.py, widgets, traffic_client, utils,
  resources/dpdk/install_dpdk.sh). The tarball-only assets (bundled
  CPython 3.10.14, FRR Docker context, the netgen-install /
  netgen-upgrade / netgen-uninstall wrapper scripts) didn't change
  in most of these releases.
* Each unchanged-tarball rebuild was waste.

### Change

`.github/workflows/build-server-tarball.yml` — dropped the
`tags:` trigger. Workflow now fires only on:

1. **`workflow_dispatch`** — manual via `gh workflow run` or the
   Actions UI's "Run workflow" button.
2. **`push` to `claude/**`** with path filter on
   `scripts/tarball/**`, the workflow file itself, or
   `pyproject.toml` — auto-tests script edits during dev without
   needing manual dispatch.

`release.yml` (the wheel + .dmg + .exe + .AppImage workflow) is
**unchanged** — it still auto-triggers on tags. Most of the
release value (the wheel for upgrades + 3 client binaries) keeps
its zero-touch publish flow.

### When you DO need a tarball for a tag

```bash
gh workflow run build-server-tarball.yml --ref v0.5.22
```

Or via Actions UI: Workflows → build-server-tarball → Run workflow
→ pick `v0.5.22` from the ref selector.

Once it completes, the tarball auto-attaches to the existing GH
release (softprops/action-gh-release is idempotent — it ADDs the
asset without touching the existing wheel/client uploads).

### Operator-side note

If you have a workflow that always grabs the latest tarball, it
will break (the v0.5.22 release won't have one until/unless
manually built). Two workarounds:

1. Pin to the last release with a tarball: v0.5.21 (always
   available, doesn't change)
2. Run `gh workflow run build-server-tarball.yml --ref vX.Y.Z`
   before consuming the release

### What this saves

| | Before v0.5.22 | After v0.5.22 |
|---|---|---|
| CI minutes per tag push | ~8 min (wheel build + tarball build in parallel) | ~3 min (wheel + clients only) |
| GitHub storage per release | ~196 MB tarball + ~50 MB clients | ~50 MB clients only (tarball on-demand) |
| Releases per day (this session's pace) | 22 → ~3 GB extra storage | 22 → 0 extra |

### Tests

6 new regression tests in
`tests/test_v0522_tarball_workflow_no_tag_trigger.py`:
* Tag trigger absent from active YAML (comments allowed for
  changelog context)
* `workflow_dispatch` retained (manual dispatch must work)
* Branch-push trigger + path filter retained (dev-loop)
* Workflow comment explains WHY + HOW to build on demand
* `release.yml` still triggers on tags (other artifacts unaffected)

1 existing test inverted: `test_workflow_triggers_on_v_tags` in
`test_v050_phase3_gui_tarball_dispatch.py` used to assert the tag
trigger existed; now asserts it's absent + workflow_dispatch is
present. Comment explains the v0.5.22 inversion + cross-references
the dedicated v0.5.22 test.

## [0.5.21] - 2026-06-07

**install_dpdk.sh no longer dies with `HOME: unbound variable`
when systemd spawns it. Plus: removed a stale developer path
that's been in the script since v0.2.x.**

Full suite: 1,618 passed, 1 skipped (+5 new tests).

### Operator-reported on srv06

The v0.5.20 client surfaced the actual error via inline log tail:

```
install_dpdk.sh exited with code 1.
Log file on server: /tmp/netgen_install_dpdk_20260608_011441.log

Last 1 log lines:
  /opt/netgen/resources/dpdk/install_dpdk.sh: line 23: HOME: unbound variable
```

The whole install died on the very first variable expansion. v0.5.20
shipping the log inline let us see this in 5 seconds instead of
"add ssh, tail file" round-trip — exactly what v0.5.20 was for.

### Root cause

`install_dpdk.sh` line 9:
```bash
set -euo pipefail
```

…and line 23:
```bash
DPDK_DIR="${DPDK_DIR:-$HOME/SURAJ/dpdk}"
```

Under `set -u`, even the `${DPDK_DIR:-$HOME/...}` default-substitution
form dies if `$HOME` itself is unset — the inner reference is
evaluated as part of the default expansion.

systemd starts services with a minimal environment. The
`netgen-server.service` unit doesn't set `Environment="HOME=..."`,
so when `/api/admin/install_dpdk` calls `subprocess.Popen(["bash",
script, "--auto"])`, the spawned bash has no `HOME` in env.

Plus a separate code-hygiene issue: `$HOME/SURAJ/dpdk` is the
original developer's local path (`SURAJ` was the dev's home
directory name). Should never have been in production code; was
slipping through because nobody had stepped through the script
with strict-mode + no HOME until now.

### Fix

**1. Script-side (`resources/dpdk/install_dpdk.sh`):**

```bash
# Defaults HOME if unset; safe under set -u.
: "${HOME:=/root}"
# v0.5.21: was "$HOME/SURAJ/dpdk" — stale dev path.
DPDK_DIR="${DPDK_DIR:-/opt/dpdk-build}"
```

`/opt/dpdk-build` matches the project's `/opt/*` convention
(`/opt/netgen-server`, `/opt/OSTG`, `/opt/netgen`). Updated all 4
sites in the script:
* Line 23 (DPDK_DIR default)
* `detect_dpdk_source()` candidates list
* Operator-prompt default in `step_clone_dpdk()`
* Documentation comment

**2. Server-side (`run_tgen_server.py`):**

```python
env = os.environ.copy()
env["TERM"] = "xterm"
env["DEBIAN_FRONTEND"] = "noninteractive"
...
env.setdefault("HOME", "/root")   # v0.5.21
```

Defense-in-depth: even if a server is upgraded to v0.5.21 but
running a stale install_dpdk.sh (e.g. operator put a local edit
in `/opt/netgen/resources/dpdk/install_dpdk.sh`), the server-side
HOME injection saves them.

### Operator-side fix without waiting for v0.5.21

```bash
ssh root@san-hp-srv06 'HOME=/root nohup bash \
  /opt/netgen/resources/dpdk/install_dpdk.sh --auto \
  > /tmp/dpdk_install_retry.log 2>&1 & echo "PID: $!"'
```

The `HOME=/root` prefix sidesteps the bug on pre-v0.5.21 hosts.

### Pattern observation

This was a **5-second-to-diagnose / 30-second-to-fix** bug —
exactly what v0.5.20's inline log tail was built for. Without
v0.5.20 the operator would have:
1. Seen "exit 1" with no context
2. SSH'd to srv06
3. `ls /tmp/netgen_install_dpdk_*.log`
4. `tail` the latest one
5. Find the HOME line
6. (Then come back to me for the fix)

With v0.5.20:
1. Wizard showed the log tail inline → pasted to me
2. Identified the bug + fix in one round

v0.5.20 paid for itself on the first install failure after it
shipped.

### Tests

5 regression tests in
`tests/test_v0521_install_dpdk_home_unbound.py` pin:

* Script has `: "${HOME:=/root}"` default
* No `SURAJ` text in any active (non-comment) line of the script
* `/opt/dpdk-build` is the new default DPDK source dir
* Server's Popen env calls `env.setdefault("HOME", "/root")`

## [0.5.20] - 2026-06-07

**Wizard now shows the install_dpdk.sh log tail inline on failure.**
Operators no longer need to ssh in to find out WHY the install
broke — last 30 lines + log path + retry button right in the
dialog.

Full suite: 1,613 passed, 1 skipped (+6 new tests).

### Operator-reported

[Screenshot showing Make DPDK Ready dialog with `Install DPDK
runtime + build tx_worker — install_dpdk.sh exit 1` and a generic
"Check the log on the server for the failure reason" message.]

The v0.5.17 polling fix did its job — caught the actual exit code
instead of falsely claiming success — but left the operator at
"exit 1" with no context. The log on the server has the actual
error; we just weren't showing it.

### Fix

`_on_install_dpdk_log_response()` already gets the last 64 KiB of
log in the response (`data["log"]` — server-side included since
v0.3.11 for the live log pane). The rc != 0 branch now:

1. Extracts `log_full = data.get("log")`
2. Renders the last 30 lines in a styled `<pre>` block
3. HTML-escapes `&`/`<`/`>` so build output with shell syntax
   doesn't break the dialog render
4. Shows `log_path` so operators can ssh + tail for full context
5. Keeps the Retry button (workflow: see error → fix → retry)

### Detail pane after v0.5.20

Before:
```
install_dpdk.sh exited with code 1.
Check the log on the server for the failure reason. Common causes:
apt timeout, network unreachable during DPDK source clone, missing
dev packages.
Log file path was shown when the step started.
```

After:
```
install_dpdk.sh exited with code 1.
Log file on server: /tmp/netgen_install_dpdk_20260607_185342.log
Common causes: apt timeout, network unreachable during DPDK source
clone, missing dev packages, libpcap-dev/libnuma-dev not installed.

Last 30 log lines:
  ┌──────────────────────────────────────────────────────────┐
  │ E: Unable to fetch some archives, maybe run apt update?  │
  │ + apt-get -y install libpcap-dev libnuma-dev ...        │
  │ Reading package lists...                                  │
  │ E: Could not get lock /var/lib/apt/lists/lock - open ... │
  │ E: Unable to lock directory /var/lib/apt/lists/          │
  │ install_dpdk.sh: Step 2/8 failed (apt install)           │
  └──────────────────────────────────────────────────────────┘

Click Retry after fixing, or ssh to the server and tail
/tmp/netgen_install_dpdk_20260607_185342.log for full context.
```

### Pattern

Same shape as v0.5.11's `_verify_running()` diagnostic dump (which
showed journalctl + port-5050 occupant + legacy svc status inline
on /api/health timeout). Different surface, identical principle:
**when something the operator needs is already in our response,
render it — don't make them ssh to find what we have.**

Codified rule: every "failed, see logs elsewhere" message in the
GUI is a UX bug. If the logs are reachable via existing endpoints,
show them inline.

### Tests

6 regression tests in
`tests/test_v0520_install_failure_log_tail.py` pin:

* Failure branch reads `data.get("log")` from response
* Failure branch reads `data.get("log_path")` for the path display
* HTML-escapes `&` and `<` (build output with `<` chars or shell
  syntax doesn't corrupt the dialog)
* Caps to last-N lines via `splitlines()[-N:]` so the dialog
  doesn't grow unbounded
* Retry button still works

## [0.5.19] - 2026-06-07

**Tier 2 DPDK UX — proactive detect + live install ETA.**

Full suite: 1,607 passed, 1 skipped (+11 new tests).

### Two items

**7. Auto-detect "DPDK not ready" on server connect**

When a new server is added via Add TGen Chassis dialog and connects
successfully, we probe `/api/dpdk/status` asynchronously. If
`is_dpdk_ready()` returns False, surface a non-blocking
`QMessageBox.Information`:

```
DPDK Setup Suggested

san-hp-srv06 isn't configured for DPDK yet.

Missing: DPDK libraries, tx_worker binary, hugepages

DPDK is required for line-rate stream generation. Without it,
streams fall back to Scapy / kernel path (slower).

Run ★ Setup DPDK now? Takes ~5-15 min on a fresh host, mostly
building the DPDK source. You can also skip and run it later
from Tools → DPDK → ★ Setup DPDK.

         [ Setup Now ]  [ Skip — set up later ]
```

* `Setup Now` selects the server in the tree + opens the Make DPDK
  Ready dialog directly. One click from "I just connected" to
  "DPDK installing".
* `Skip` dismisses. Doesn't persist — operator gets prompted again
  on next add (by design; they might have intentionally skipped on
  a VM but want the prompt on a real host).

Closes the gap that prompted this whole sub-thread: operator did a
fresh tarball install, opened Make DPDK Ready, the wizard claimed
all-done in 30 seconds (v0.5.17 bug), Verify showed all ✗. With
v0.5.19, the moment they connect the freshly-installed server,
they see the prompt that says "DPDK isn't set up — want to fix
that now?"

Async probe via `_DpdkApiWorker` (UI doesn't freeze on the 4 s
timeout). Probe failure is silent — if the server is too old to
have `/api/dpdk/status` or there's a network blip, we don't badger
the operator with an error they didn't ask for.

**8. Live elapsed time + ETA in Make DPDK Ready during install**

The v0.5.17 polling loop already showed phase progress (`Step 5/8:
Building DPDK · ninja 47%`) but not "how much longer". For a 10-min
install, that's the most useful information.

v0.5.19 tracks `time.monotonic()` at install start, computes elapsed
per poll, derives ETA from `overall_pct`. Detail pane now shows:

```
Installing DPDK runtime + building tx_worker
Step 5/8 · Building DPDK · ninja 47% · ~52% overall · elapsed 3:24 · ETA ~3:08
```

ETA gated on `overall_pct >= 5` to avoid wildly-wrong estimates
during apt+clone phase. Also capped to <60 min sanity check
(prevents weird display if the install hangs). Uses `monotonic` not
wall-clock so a clock jump during install doesn't poison the math
(carry-over lesson from v0.5.9).

`_fmt_mmss()` helper at module level — testable + reusable, returns
"0:05" / "1:05" / "10:00" form.

### Tests

11 regression tests in `tests/test_v0519_autodetect_and_eta.py`
pin every contract:

* Add-server flow calls `_check_dpdk_and_suggest_setup` per added URL
* Auto-detect uses `_DpdkApiWorker` (async)
* Probe callback uses `is_dpdk_ready()` (no drift from wizard)
* Banner offers "Setup Now" button wired to
  `show_dpdk_make_ready_dialog`
* Failure path is silent (no probe-error popups)
* `_start_install_dpdk_poll` captures `monotonic` start
* Response handler shows elapsed + ETA
* ETA gated on `overall_pct >= 5`
* `_fmt_mmss` exists at module level; pads correctly for sub-10 sec

### What this doesn't change

* No server-side changes
* No new endpoints (reuses /api/dpdk/status + /api/admin/install_dpdk/log)
* Banner is per-add-event, not periodic — doesn't nag if the
  operator dismisses it

## [0.5.18] - 2026-06-07

**Tools → DPDK menu restructured + 5 ergonomic optimizations.**
Operator-driven cleanup of the menu we just spent v0.5.15→v0.5.17
fixing functionally. Same engine — better menu / better feedback.

Full suite: 1,596 passed, 1 skipped (+16 new tests in
`test_v0518_dpdk_menu_optimizations.py`, 2 existing tests updated
for new menu layout).

### Why this release

Operator question that prompted the work: "what is the difference
between Make Server Ready, Quick Start, DPDK Status, Configure
IOMMU, Load VFIO?" — there shouldn't BE a difference to think
about. The menu had 10 flat items with overlapping responsibilities
(two wizards doing the same thing, two read-only dialogs, 5 atomic
actions cluttering the top level).

### What changed

**1. Menu restructured** — 10 flat items → 3 tiers:

Before:
```
Tools → DPDK
├── Quick Start Wizard...
├── Make DPDK Ready...
├── Blast a Flow...
├── ─────────
├── Status...
├── Bind / Unbind Interface...
├── ─────────
├── Verify Installation
├── Configure Hugepages...
├── ─────────
├── Configure IOMMU...
└── Load VFIO Modules
```

After:
```
Tools → DPDK
├── ★ Setup DPDK...              ← single canonical entry
├── Blast a Flow...
├── ─────────
├── Diagnostics...               ← Status + Verify merged
├── ─────────
└── Advanced ▸                   ← atomic actions submenu
    ├── Quick Start Wizard...    ← demoted (alternative UI)
    ├── Bind / Unbind Interface...
    ├── Configure Hugepages...
    ├── Configure IOMMU...
    └── Load VFIO Modules
```

`★ Setup DPDK` is `show_dpdk_make_ready_dialog` under the hood —
zero engine changes. Quick Start Wizard is still reachable but
under Advanced where first-time operators won't trip over the
two-wizards confusion.

**2. Diagnostics dialog** — new `widgets/dpdk_diagnostics_dialog.py`
merges what were two separate dialogs (Status full, Verify quick
4-check) into one with tabs. Operator opens one menu item, sees
both views. No more "which one do I click?"

**3. Time estimates in wizard rows** — `Action.eta` (new field on
the orchestrator's dataclass) is rendered in the `_StepRow`
pending/running label. So the wizard now shows:

```
●  Install DPDK runtime + build tx_worker  [5-10 min (apt + DPDK clone + meson + ninja)]
○  Enable IOMMU in kernel cmdline  [<5 sec + REBOOT (~1-2 min for host to come back)]
○  Load vfio + vfio-pci kernel modules  [<1 sec (modprobe)]
○  Allocate 1024 × 2MB hugepages  [<2 sec (write /sys + persist to /etc/sysctl.d)]
○  Bind a NIC to vfio-pci  [<5 sec (per NIC) + GUI picker time]
```

Operators see what they're getting into before clicking Run, not 8
minutes in. Eta disappears once the row transitions to ok/fail/skip
to keep the post-completion view clean.

**4. Shared TTL cache for `/api/dpdk/status`** —
`get_cached_dpdk_status` / `cache_dpdk_status` /
`invalidate_dpdk_status_cache` in `traffic_client.dpdk_menu_actions`.

* Status-bar chip's 30s poll populates the cache
* Diagnostics dialog hits the cache before doing fresh HTTP
* `_DpdkApiWorker.run()` auto-invalidates on any successful POST
  to `/api/dpdk/*` or `/api/admin/install_dpdk` so stale state
  doesn't survive a bind / unbind / hugepages-change

Cuts ~500ms off dialog opens when the chip just polled. Mutation
invalidation means the next read always reflects current state.

**5. Status-bar chip tooltip leads with "Missing: ..."** — pre-
v0.5.18 the tooltip enumerated each subsystem with its state
(libdpdk: ok, tx_worker: missing, ...) — useful but you had to
read all 5 rows to figure out what was wrong. Now:

```
Missing: DPDK libs, tx_worker — click chip to open ★ Setup DPDK

DPDK readiness:
  • DPDK libraries: missing
  • tx_worker binary: missing
  • Hugepages: not allocated
  • IOMMU: off
  • vfio-pci: not loaded
```

One-glance diagnosis instead of 5-row read.

### What this doesn't change

* No server-side changes (no new endpoints; cache is client-side
  only; eta is client-side metadata)
* Same orchestrator engine, same wizards, same atomic actions
* Operators can still find every old menu item (Quick Start is
  under Advanced, Status/Verify are inside Diagnostics)
* No protocol or stream behavior changes

### Tests

16 new regression tests in
`tests/test_v0518_dpdk_menu_optimizations.py` pin every contract:
* Menu has ★ Setup DPDK at top, Diagnostics next, Advanced ▸
  submenu at bottom
* Advanced submenu contains Quick Start + all 5 atomic actions
* `DpdkDiagnosticsDialog` exists with QTabWidget + queries both
  endpoints + uses TTL cache
* `Action.eta` field exists; INSTALL_DPDK has min/sec ETA; IOMMU
  has REBOOT warning in ETA
* `_StepRow._render` includes eta in pending/running labels
* Cache helpers exist; TTL is sane (30s); POST mutations invalidate
* Chip tooltip has "Missing:" summary line; chip populates cache

2 existing tests updated for new menu layout (Make DPDK Ready
renamed to ★ Setup DPDK; Quick Start moved to Advanced submenu).

## [0.5.17] - 2026-06-07

**Make DPDK Ready wizard now waits for `install_dpdk.sh` to ACTUALLY
finish — not just for the subprocess to spawn.**

Full suite: 1,580 passed, 1 skipped (+9 new tests).

### Operator-reported

> tried making server ready with dpdk using make server dpdk ready
> and completed all the steps, however verify installation, shows
> below..
>
>   DPDK Libraries: ✗
>   DPDK Packet Generator (tx_worker): ✗
>   Hugepages: ✗
>   Kernel Modules: ✓

### Root cause

The wizard's `_on_step_done()` callback fired the moment
`/api/admin/install_dpdk` returned HTTP 200. But that endpoint
**only confirms the script was SPAWNED** — `install_dpdk.sh --auto`
runs for 5-10 minutes in the background. The endpoint returns
immediately with a `log_path` the client can poll.

The wizard treated the immediate 200 as "step done" and marched
forward through `ALLOCATE_HUGEPAGES` / `LOAD_VFIO` /
`BIND_INTERFACE`. All shown ✓ in the wizard. But DPDK wasn't
actually installed yet (still building). Verify Installation
afterward exposed the truth.

### Fix

`widgets/dpdk_make_ready_dialog.py:_on_step_done` now special-cases
`ActionKind.INSTALL_DPDK`:

1. **Doesn't** call `row.set_state("ok")` on the spawn 200.
2. Spawns a `QTimer(5000)` that polls `/api/admin/install_dpdk/log`.
3. Each poll updates the detail pane with parsed phase
   (current_step / total_steps / step_name / ninja % / overall %).
4. On `running=False`:
   * `return_code == 0` → mark ✓, advance to next step
   * `return_code != 0` → mark ✗ with exit code, show Retry
   * `return_code is None` → race (server polled between proc.poll()
     and finished_at write); retry one more tick

Plus:
* Tolerates up to 3 consecutive HTTP errors against the log
  endpoint before giving up (single network blip doesn't abort
  the install)
* `_stop_install_dpdk_poll()` is idempotent (no NoneType crashes
  on dialog close)
* Each GET passes `?offset=N` so the server only ships log bytes
  appended since the last poll

### Why this didn't surface in CI

The wizard's pre-v0.5.17 behavior had a self-defeating test gap:
* CI never actually runs `install_dpdk.sh` (it's a 10-min apt+meson+
  ninja chain that needs root + bare-metal hardware for the tx_worker
  link step)
* The dialog smoke tests check construction, not endpoint completion
  semantics
* No mock for the `/api/admin/install_dpdk/log` polling contract

So the wizard's "200 == done" assumption was untested against a
slow-server response. Same class as the v0.5.7 false-positive
("CI green because of staging-tree state that isn't on the
operator's host") — different surface, same lesson.

### Operator workflow improvement

| Step | Before v0.5.17 | After v0.5.17 |
|---|---|---|
| Click Make DPDK Ready → Run | Same | Same |
| Wizard hits Install DPDK | 200 returns in <1s | Same |
| Wizard's row says... | "✓ Install DPDK runtime + build tx_worker" | "Step 5/8: Building DPDK · ninja 47%" |
| Wizard advances | Immediately (BUG — install still running) | Only after `running=False && rc=0` |
| Time to next step | <1s | ~5-10 min (real install duration) |
| All steps ✓ shown | After ~30s total (lie) | After ~10 min total (truth) |
| Verify Installation | All ✗ — DPDK still building | All ✓ — install actually done |

### What this doesn't change

* No server-side changes — uses the existing
  `/api/admin/install_dpdk/log` endpoint
* No changes to `install_dpdk.sh` itself
* Other wizard steps (IOMMU, VFIO, hugepages, bind) keep their
  immediate-completion semantics — they're synchronous HTTP endpoints
  that block until done

### Tests

9 regression tests in `tests/test_v0517_install_dpdk_polling.py`
pin:

* `_on_step_done` special-cases `INSTALL_DPDK` before generic ✓ path
* `_start_install_dpdk_poll` uses `QTimer` (UI stays responsive)
* Poll endpoint is `/api/admin/install_dpdk/log`
* Response handler advances on `rc == 0`
* Response handler fails (with exit code) on `rc != 0`
* Response handler keeps polling while `running=True`
* Tolerates up to N consecutive HTTP errors (configurable threshold)
* `_stop_install_dpdk_poll` is idempotent
* Version ≥ 0.5.17

## [0.5.16] - 2026-06-07

**Status LED flips red within seconds of Reboot Physical Server, and
the app no longer freezes intermittently during the 3-5 min reboot
window.**

Full suite: 1,571 passed, 1 skipped (+11 new tests).

### Operator-reported

> after server reboot from the add tgen dialog, app started freezing
> intermittently and server status still shows green

### Two distinct root causes

**1. Status LED stayed green** — `poll_server_health()` caught all
exceptions and silently `pass`ed. The server's `online` flag never
flipped, the LED kept showing green even though `/api/admin/health`
was returning ECONNREFUSED.

**2. App froze intermittently** —
`ConnectionManager.get()` uses `Retry(total=3, backoff_factor=1)` on
the shared session adapter. Each request to a dead server burned up
to **~7 seconds** of retry-with-backoff before failing. With:

* stats poll every 2 s → new worker spawns
* health poll every 30 s → another worker
* Each worker holds a session connection for ~7 s on a dead host
* `pool_maxsize=20`, but workers signal back to the UI thread

… the signal queue stacked up faster than the UI could drain it,
producing the "intermittent freezing" feel during the entire reboot
window.

### Fix

**A. `ConnectionManager.quick_get(url, timeout=4)`** — bypasses the
retry-configured adapter entirely (uses bare `requests.get`). For
periodic pollers where fast failure beats blocking retry. User-
initiated calls (test connection, manual probe) keep using `get()`
so they benefit from retry on transient blips.

**B. `poll_server_health()` flips offline after N=2 failures** —
worker now emits `(server, health_or_None, ok_bool)` instead of
silently dropping the failure case. `_apply_server_health()`
increments `server["health_fail_count"]` on `ok=False`; at the
threshold (`HEALTH_OFFLINE_AFTER_N_FAILURES = 2`) it flips
`online=False` and calls `update_server_status_icon(server, False)`.
Successful probes reset the counter to 0, so transient blips don't
accumulate over hours.

With the 30 s health-poll cadence, this gives **~60 s from
unreachability to LED-red on its own** — fast enough that operators
don't sit staring at a green-LED-and-frozen-app, slow enough to
absorb a single dropped packet.

**C. `AddTGenDialog.server_rebooted` signal** — emitted on
`/api/system/reboot` 200. The dialog now tells the parent main
window that the host is going down, before the polling cadence can
even fire once. `_on_server_rebooted(host_port)` in
`menu_actions.py`:

* finds the matching server by address substring
* flips `online=False` + `health=None`
* resets `health_fail_count=0` so when the server comes back, the
  health-poller starts fresh (not stuck at N-1 failures that would
  re-flip offline on the first post-reboot network blip)
* calls `update_server_status_icon(server, False)` → LED instantly
  goes red

**Combined effect:** LED goes red **immediately** when operator
clicks Reboot Physical Server (via signal); stats pollers
auto-filter on `online=True` so they skip the dead host (no more 7s
retries piling up); health poller fast-fails and resets state
cleanly when the server returns.

### Operator workflow improvement

| Step | Before v0.5.16 | After v0.5.16 |
|---|---|---|
| Click "Reboot Physical Server" | OK | OK |
| Confirm in dialog | OK | OK |
| `/api/system/reboot` returns 200 | LED stays green | **LED → red immediately** |
| Server starts rebooting | Stats pollers spam dead host (7s retry each) | Stats pollers skip (server marked offline) |
| First minute | App freezes intermittently from signal-queue backup | App responsive — no workers hitting the dead host |
| 30 s of unreachability | LED still green | LED already red from signal; health poll just confirms |
| 60 s of unreachability | LED still green | LED already red |
| 5 min: server comes back | Stats start working again | Stats start working again |
| Manual retry click | OK | OK |

### Tests

11 regression tests in `tests/test_v0516_reboot_state_fixes.py`
pin:

* `ConnectionManager.quick_get` exists + uses bare `requests.get`
  (not `self.session.get`) to bypass the retry adapter
* `poll_server_health` uses `quick_get`
* Worker emits `(server, health_or_None, ok_bool)` with failure path
* `_apply_server_health` flips offline after N failures via
  `update_server_status_icon`
* Success path resets `health_fail_count`
* `AddTGenDialog.server_rebooted` signal declared
* `_reboot_physical_server` emits on HTTP 200
* `menu_actions` connects the signal
* `_on_server_rebooted` finds matching server + flips offline + calls
  `update_server_status_icon`
* `_on_server_rebooted` resets `health_fail_count`

### What this doesn't change

* No server-side changes — same `/api/system/reboot` endpoint
* No changes to the periodic-poll cadence (still 30s health, 2s stats)
* User-initiated `get()` still uses retry adapter (transient blips
  on Test Connection still get the retry benefit)

## [0.5.15] - 2026-06-07

**Make DPDK Ready wizard now offers inline reboot after enabling
IOMMU — no more alt-tab to a terminal.**

Full suite: 1,560 passed, 1 skipped (+7 new tests).

### Operator request

> when installing dpdk using make dpdk ready, it enables iommu
> and prompt user to if reboot is required, also let user reboot
> from the prompt it self.

### Before

After the wizard ran `/api/dpdk/iommu` (which writes
`intel_iommu=on iommu=pt` / `amd_iommu=on iommu=pt` to
`/etc/default/grub` + `update-grub`), the IOMMU kernel cmdline
change doesn't take effect until reboot. The wizard's previous
behavior was a `QMessageBox.information` saying "Reboot the server,
then click Make DPDK Ready again." Operator had to:

1. Close the dialog
2. SSH into the server
3. `sudo reboot`
4. Wait for it to come back
5. Re-open netgen, click Make DPDK Ready again

### After (v0.5.15)

`QMessageBox.Question` with three buttons:

| Button | Role | What it does |
|---|---|---|
| **Reboot Now** (default, Enter) | AcceptRole | POST /api/system/reboot, dialog stays open showing live status |
| **I'll Reboot Later** (Escape) | RejectRole | Just close — operator reboots manually from their terminal |
| **Cancel** | (implicit) | Leave dialog open so operator reviews log first |

The `Reboot Now` path uses the same `/api/system/reboot` endpoint
the AddTGenDialog's "Reboot Physical Server" button uses (v0.5.2).
Server replies 2xx first, then schedules `systemctl reboot` ~3 s
later — so the HTTP response reaches the client cleanly before the
host goes down.

### Robustness — server-too-old handling

If the target server predates v0.5.2 (no `/api/system/reboot`
endpoint), POSTing returns 404. `_on_reboot_response()` catches that
specifically and surfaces:

```
This server is too old to support remote reboot
(no /api/system/reboot — added in v0.5.2).

Reboot manually:
  ssh root@<host> 'sudo reboot'

Then re-run Make DPDK Ready.
```

Operator gets the exact command, not a generic "request failed".

### Success messaging

On 2xx, the dialog updates the detail pane:

```
✓ Reboot scheduled. Server replied OK and will restart in ~3 s.
Wait for it to come back online (typically 30–60 s), then re-run
Make DPDK Ready — the wizard will skip the IOMMU step (now active)
and continue from there.
```

So operators don't wonder if the wizard hung when the server
inevitably stops responding to subsequent polls during the reboot.

### Tests

7 regression tests in `tests/test_v0515_dpdk_reboot_prompt.py`:

* `_prompt_reboot` helper exists (encapsulation)
* Reboot Now button is AcceptRole + setDefaultButton (Enter fires it)
* I'll Reboot Later is RejectRole (Escape dismisses)
* `_trigger_reboot` POSTs to /api/system/reboot via async worker
* 404 fallback mentions v0.5.2 + provides ssh command
* Success path mentions waiting for server to come back online
* `_on_step_done`'s `needs_reboot` branch invokes `_prompt_reboot`

### What this doesn't change

* No new server endpoint — reuses `/api/system/reboot` from v0.5.2
* No changes to the IOMMU configure endpoint or GRUB write logic
* No changes to the DPDK orchestrator / action plan
* Only changed: the dialog UX after IOMMU success

## [0.5.14] - 2026-06-07

**In-app Install Guide rewritten for the v0.5.x tarball architecture.
No code-path changes — documentation only.**

Full suite: 1,545 passed, 1 skipped (no test deltas; existing TOC
test still validates ≥20 sections, now ~30).

### What changed

`widgets/stream_dialog.py:_INSTALL_GUIDE_HTML` got three updates:

**1. New §0 — v0.5.x install architecture (★ current)**

Front-of-guide orientation for operators landing on the dialog
without context. Covers:

* What the v0.5.0+ tarball drops on disk (~730 MB total: 95 MB
  bundled CPython, 537 MB venv, 94 MB FRR Docker image, plus
  small bits).
* What it does NOT touch (no apt, no system Python, no firewall,
  no user accounts, no shell-rc).
* Where to start in the dialog (Fresh Install → Tarball).
* The 7 install-pipeline contracts CI now validates (v0.5.6→v0.5.13
  cascade, table form), so future operators have a roadmap of
  what's already hardened.
* Expected end-of-log on success (paste-comparable).

**2. §8 rewritten — What lives where on the target**

Was: stale `/opt/netgen/` + `python3.13/dist-packages/` paths from
the pre-v0.5.0 era. Now: two tables, current v0.5.x layout first
(with row per artifact + v0.5.10/v0.5.12/v0.5.13 fix annotations)
and legacy v0.4.x layout second (for un-migrated hosts).

**3. New §11 — v0.5.x troubleshooting recipes**

Six SSH one-liners for the failure modes operators hit during the
v0.5.6→v0.5.13 cascade:

* 11a. Server not responding on `/api/health` (port conflict /
  legacy svc).
* 11b. `ostg-server: No such file or directory (203/EXEC)` —
  manual shebang rewrite for pre-v0.5.12 tarballs.
* 11c. `tar: file is N seconds in the future` — fix the clock or
  use `--warning=no-timestamp`.
* 11d. Wheel upgrade `externally-managed-environment` — manual
  `--break-system-packages` to get past the v0.5.5→v0.5.6 gap.
* 11e. Compat warnings (legacy `/opt/OSTG/` and `/opt/netgen/`
  exist) — consolidation recipe.
* 11f. FRR Docker build "frr.conf.template: not found" — manual
  rebuild with correct context for pre-v0.5.11 tarballs.

All have been fixed in the install pipeline at v0.5.13+; the
section is for operators still on intermediate tarballs or
diagnosing what a given fix actually did.

### Why ship a doc-only release

Operator went through the full v0.5.6→v0.5.13 cascade hitting
each bug live. The in-app Install Guide had been frozen at the
v0.4.x flow (legacy `install_ostg_complete.py` end-to-end). Anyone
reading the in-app help today would find guidance that doesn't
match the install they're running. The audit-driven v0.5.13 closed
the code gaps; this release closes the documentation gap.

The legacy §1–§10 sections (Upgrade tab, prebuilt artifacts,
`install_ostg_complete.py` deep-dive, RDMA setup) are unchanged —
still accurate for upgrade flows and v0.4.x migration scenarios.

## [0.5.13] - 2026-06-07

**Proactive audit release. Closes two latent bugs that would have
hit the next fresh-host install — found by reading the runtime
code, not by an operator report.**

Full suite: 1,545 passed, 1 skipped (+5 new tests).

### Why this release exists (and what's NOT in it)

After 6 consecutive operator-driven releases (v0.5.6 → v0.5.12), I
audited every hardcoded absolute path in the runtime to surface
what else could still bite. Two bugs found that srv06 didn't hit
because srv06 has a legacy /opt/OSTG/ install — a fresh host
without it would crash.

### Bug 1 — Device database default path mismatch

`utils/device_database._resolve_db_path()` resolution order:

```
1. NETGEN_DB_PATH env var       (unset on fresh install)
2. OSTG_DB_PATH env var          (unset)
3. /opt/netgen/database.db       (doesn't exist — install is at
                                  /opt/netgen-server/)
4. /opt/OSTG/device_database.db  (only exists on hosts with legacy
                                  v0.4.x install)
```

On a fresh host with NEITHER `/opt/netgen/` NOR `/opt/OSTG/`,
resolution returns `/opt/netgen/database.db` and the server tries
to open it — parent dir doesn't exist → sqlite errors → server
crashes on first DB operation.

Same shape applies to `run_tgen_server.py`'s
`_resolve_ai_settings_path()` (defaults to
`/opt/netgen/.netgen_ai_server_settings.env`).

srv06 didn't hit it because its legacy `/opt/OSTG/` provided the
fallback path. **Any new host without that legacy dir would have
hit this on first start.**

### Bug 2 — FRR image legacy tag missing

`utils/frr_vrf.py:32` falls back to `"ostg-frr:latest"` when the
primary `_resolve_frr_image()` call throws:

```python
try:
    from utils.frr_docker import _resolve_frr_image
    self.image_name = _resolve_frr_image(self.client)
except Exception:
    self.image_name = "ostg-frr:latest"  # legacy fallback
```

`utils/frr_docker.py:183` adds the legacy tag when it builds via
the lazy self-heal path, BUT only then. Installs that complete
without ever triggering lazy build have only `netgen-frr:latest`
— the fallback dangles, points at a non-existent image.

### Fix

```python
# netgen-install: new compat symlink (mirrors v0.5.10 /opt/OSTG)
def _create_netgen_compat_symlink(install_root):
    """/opt/netgen → /opt/netgen-server"""

# netgen-install: dual-tag FRR image after docker build
docker tag netgen-frr:latest ostg-frr:latest
```

Mirrors `utils/frr_docker.py:183`'s existing dual-tag logic. Now
the install-time path produces the same image tags as the lazy
self-heal path.

### What this DOESN'T fix (yet — documented for next round)

Audit also found, with these severity ratings:

* **Low** — `utils/dhcp.py` writes to `/etc/dnsmasq.d/`. Server
  creates the dir if missing; works on most distros; might trip
  SELinux. DHCP feature only.
* **Low** — `run_tgen_server.py:14038` modifies `/etc/default/grub`
  for DPDK hugepages. Standard for DPDK; not triggered without DPDK.
* **OK** — All `/opt/OSTG/resources/dpdk/...` references resolve
  correctly via v0.5.10's `/opt/OSTG → share/netgen` symlink.

Codified rule for future maintainers: **before adding a new
hardcoded path, check that the default works on a host with no
legacy /opt/OSTG/.**

### Pattern (final)

v0.5.13 is the first release this session that DIDN'T come from an
operator-reported bug. Every prior release in the v0.5.6→v0.5.12
chain was reactive. The audit pattern goes:

1. After a class of bug bites, add the regression test
2. Then read the codebase looking for the same class
3. Ship fixes for anything found preemptively

v0.5.13 = step 2 + 3 for the "hardcoded paths from legacy era"
class. The audit findings are catalogued above; future releases
can pick up the remaining low-severity items if they become
problematic.

## [0.5.12] - 2026-06-07

**ostg-server (and every other entry-point script pip installed)
now has a shebang that actually resolves on the operator's host.**

Full suite: 1,540 passed, 1 skipped (+4 new tests).

### Operator-reported

san-hp-srv06 after v0.5.11's FRR build + diagnostic dump landed. The
install completed FRR cleanly, then `_verify_running()` timed out.
The new v0.5.11 diagnostic dump made the cause obvious:

```
[VERIFY] Server did not respond on http://localhost:5050/api/health within 60s.
[VERIFY] Diagnostic dump follows ──
$ journalctl -u netgen-server.service -n 30 --no-pager
Failed to execute /opt/netgen-server/netgen-venv/bin/ostg-server:
  No such file or directory
netgen-server.service: Main process exited, code=exited, status=203/EXEC
$ ss -tlnp sport = :5050
(empty — nothing listening)
$ systemctl is-active ostg-server.service
inactive
```

Exit code 203/EXEC is Linux's "shebang interpreter missing"
signal. The `ostg-server` script existed; its shebang was:

```
#!/home/runner/work/netgen/netgen/netgen-server-0.5.11/netgen-venv/bin/python3
```

That path only exists on the GitHub Actions runner.

### Root cause

v0.5.7 fixed shebangs for `bin/netgen-install`, `bin/netgen-upgrade`,
`bin/netgen-uninstall` — the three scripts the CI workflow's step
5 explicitly rewrites with `sed -i '1s|.*|#!/opt/netgen-server/
netgen-venv/bin/python|'`.

That rewrite covered the install scripts but MISSED the dozens of
entry-point scripts pip installs in `netgen-venv/bin/`:
`ostg-server`, `ostg-client`, `netgen-cli`, `ostg-docker-install`,
`pip`, `pip3`, `flask`, `pytest`, etc. All of those had CI-runner
shebangs.

systemd `ExecStart=/opt/netgen-server/netgen-venv/bin/ostg-server`
→ kernel loads the script → reads `#!/home/runner/...` → execs
that interpreter → ENOENT → 203/EXEC.

### Fix

CI workflow step 3b (new): after `pip install` lands the wheel,
walk every file in `netgen-venv/bin/`, detect CI-runner shebangs,
and rewrite them to `#!/opt/netgen-server/netgen-venv/bin/python`
(which is a relative symlink to `../../python-runtime/bin/python3`
thanks to v0.5.7, so it resolves at any extract location).

Plus a post-rewrite guard: `grep -l '^#!/home/runner' netgen-venv/
bin/*` must come up empty. If anything leaked through, fail the
build loudly.

Plus round-trip step (which extracts to a fresh location and
revalidates): for each of `ostg-server`, `ostg-client`, `netgen-cli`,
`ostg-docker-install`, confirm the shebang's interpreter is an
executable file at the extract location. Mirrors what systemd's
ExecStart will do — catches the regression class regardless of
which specific shebang pattern slipped through.

4 regression tests pin all three contracts.

### Operator-side note

When an operator runs `netgen-upgrade /path/to/new.whl`, pip
generates fresh shebangs based on the venv's own python
(`/opt/netgen-server/netgen-venv/bin/python`) — those are already
correct. The bug only affected the CI initial-build flow's pip
invocation, where pip's `sys.executable` was the staging path.

### Operator workflow for srv06 right now

Quick fix without re-downloading:

```bash
ssh root@san-hp-srv06 << 'EOF'
for f in /opt/netgen-server/netgen-venv/bin/{ostg-server,ostg-client,netgen-cli,ostg-docker-install}; do
  [ -f "$f" ] && sudo sed -i "1s|.*|#!/opt/netgen-server/netgen-venv/bin/python|" "$f"
done
systemctl restart netgen-server.service
sleep 5
curl -s http://localhost:5050/api/health
EOF
```

That should return `{"netgen_version":"0.5.11", ...}` and srv06 is
fully online. FRR is already built (from the v0.5.11 install run);
the v0.5.11 install + this manual shebang fix = working v0.5.11
server.

For a clean v0.5.12 install: download Netgen-TrafficGenerator-0.5.12
client, Fresh Install via SSH with the v0.5.12 tarball.

### Pattern (continued)

7th consecutive release this session. The v0.5.7 fix was correct
for what it covered (the 3 install scripts in `bin/`), but missed
the much larger surface in `netgen-venv/bin/`. CI didn't catch it
because the round-trip step only verified `bin/netgen-install`'s
shebang chain — not the entry-point scripts pip lays down inside
the venv.

Codified rule extension:

> CI must validate the shebang of every script systemd or systemd-
> like external tools could exec. Not just the install scripts.

Round-trip step now does that for ostg-server (systemd ExecStart
target), and the entry-point pattern covers any future pip-installed
scripts that match the same shape.

## [0.5.11] - 2026-06-07

**FRR Docker build now finds its sibling files. Plus install log
self-diagnoses when /api/health fails to come up.**

Full suite: 1,536 passed, 1 skipped (+5 new tests).

### Operator-reported

san-hp-srv06 after v0.5.10 cleared the preflight gate:

```
[4/7] COPY frr.conf.template /etc/frr/frr.conf.template
ERROR: failed to calculate checksum of ref ...:
  "/frr.conf.template": not found
[6/7] COPY start-frr.sh /usr/local/bin/start-frr.sh
ERROR: failed to calculate checksum of ref ...:
  "/start-frr.sh": not found
[WARNING] [FRR] Docker build failed: ... non-zero exit status 1.
... (FRR is non-fatal; install continues)
[VERIFY] Server did not respond on http://localhost:5050/api/health within 60s.
[client] installer exit rc=1
```

### Fix 1 — FRR Docker build context

The v0.5.0 workflow copied Dockerfile.frr to BOTH
`share/netgen/Dockerfile.frr` (build context root) AND
`share/netgen/ostg_docker/Dockerfile.frr` (subdir). But the
`COPY frr.conf.template ...` directive resolves relative to the
build context, and the template+startup-script siblings live in
ostg_docker/, not at share/netgen/ root.

netgen-install was using `share/netgen/Dockerfile.frr` with
`share/netgen/` as context. Every COPY failed.

v0.5.11 `_build_frr_image()` uses `share/netgen/ostg_docker/`
as both `-f` source AND build context. All 3 files coexist there.
With a precondition check that all 3 actually exist — so a corrupted
tarball surfaces as a clear "FRR layout invalid" error, not a
confusing docker build crash.

### Fix 2 — Self-diagnosing /api/health timeout

When `_verify_running()` times out, the install log used to say:

```
[VERIFY] Server did not respond on http://localhost:5050/api/health
within 60s. Check: journalctl -u netgen-server.service -n 50 --no-pager
```

That puts the work on the operator. v0.5.11 does it inline:

```
[VERIFY] Server did not respond on http://localhost:5050/api/health within 60s.
[VERIFY] Diagnostic dump follows ──
$ journalctl -u netgen-server.service -n 30 --no-pager
<full 30 lines>
$ ss -tlnp sport = :5050
<who's holding the port>
$ systemctl is-active ostg-server.service
active   ← AHA, that's the blocker
[VERIFY] ── end diagnostic dump
[VERIFY] If 'ostg-server.service' is active above, run:
  sudo systemctl disable --now ostg-server.service && \
  sudo systemctl restart netgen-server.service
```

The most common cause on v0.4.x → v0.5.x migration is the legacy
`ostg-server.service` still bound to :5050. Operators now see this
in the install log with the exact fix command.

### CI gap closed

Round-trip step now parses Dockerfile.frr's COPY directives and
verifies every sibling file exists in the build context. Without
this, the next sibling-file addition would silently break the next
operator's install.

Plus a source-tree-level test: any COPY src in Dockerfile.frr must
exist as a real file in `ostg_docker/`. Catches the regression at
edit time, before the commit even lands.

### Operator workflow for srv06 right now

```bash
# 1. Diagnose
ssh root@san-hp-srv06 'systemctl list-units --all "*ostg*" "*netgen*"'

# 2. Most likely fix (legacy v0.4.x service still on :5050)
ssh root@san-hp-srv06 'systemctl disable --now ostg-server.service; \
  systemctl restart netgen-server.service; sleep 5; \
  curl -s http://localhost:5050/api/health'
```

After that, the v0.5.11 install will succeed cleanly AND the FRR
image will build correctly the first time.

### Pattern (continued)

6th consecutive release this hour where CI was green but the
operator hit a real bug. This one was different shape: not "CI
tested something the operator doesn't" but **"the CI smoke
test was checking pre-bundled-venv import, not Docker build
context"**. Round-trip now parses Dockerfile.frr's COPY directives —
the only way to catch "Docker build context broken" without
actually running docker build in CI.

Codified rule: **for every external tool the install script calls
(docker, systemctl, pip), CI must validate the contract that tool
will check.** Not the python-import side of it.

## [0.5.10] - 2026-06-07

**Two latent bugs from v0.5.0 finally hit on srv06 after the clock-
skew gate cleared:** the tarball was packing `resources/dpdk/` at
the wrong path, and the runtime expected a `/opt/OSTG/` install
location that the v0.5.0+ tarball never created.

Full suite: 1,531 passed, 1 skipped (+6 new tests).

### Operator-reported

san-hp-srv06 after v0.5.9 clock fix let tar finally succeed:

```
[INFO] [STARTUP] Install root: /opt/netgen-server
[INFO] [PRE-FLIGHT] Checking environment...
[INFO]   - OS: ubuntu 24.04
[ERROR] Install root /opt/netgen-server is missing expected files:
[ERROR] - /opt/netgen-server/share/netgen/resources/dpdk
[ERROR] The tarball must be extracted intact to a single root.
[client] installer exit rc=3
```

### Bug 1 — CI path mismatch (latent since v0.5.0)

netgen-install's `_preflight` check at line 197:
```python
required = [
    install_root / "share" / "netgen" / "resources" / "dpdk",
    install_root / "share" / "netgen" / "Dockerfile.frr",
    ...
]
```

But the CI workflow was doing:
```bash
cp -r resources/dpdk "$ROOT/share/netgen/"
```

That lands files at `share/netgen/dpdk/`, missing the `resources/`
parent. Mismatch shipped in every tarball from v0.5.0 through
v0.5.9.

**Why CI never caught it:** the v0.5.7 round-trip step added
`exec bin/netgen-install` smoke-test, but the script exits on
`_require_root()` BEFORE the layout-check runs. The preflight was
literally never validated in CI.

Fix: workflow copies into `share/netgen/resources/` (with parent
dir), AND round-trip step now explicitly checks all four required
layout paths exist post-extract (mirroring `_preflight`'s contract
without needing root).

### Bug 2 — `/opt/OSTG` runtime path hardcodes (latent since rebrand)

Even after fixing bug #1, the install would complete but DPDK ops
would all fail at runtime. The runtime code hardcodes paths like:
```python
# run_tgen_server.py:12840
"/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker"
# run_tgen_server.py:12963
dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
```

These date back to the pre-tarball system-pip era when
`install_ostg_complete.py` deployed everything to `/opt/OSTG/`.
The v0.5.0+ tarball installs to `/opt/netgen-server/` —
`/opt/OSTG/` doesn't exist, all DPDK ops fail with "file not
found".

The right long-term fix is to rewrite the runtime to look at
`/opt/netgen-server/share/netgen/resources/dpdk/...` (tracked as
a follow-up). For v0.5.10, surgical fix: netgen-install creates
a compat symlink:
```python
def _create_ostg_compat_symlink(install_root):
    """/opt/OSTG → /opt/netgen-server/share/netgen"""
```

So the existing `/opt/OSTG/resources/dpdk/...` paths resolve
correctly with zero runtime-code changes. Idempotent — re-running
netgen-install doesn't stomp on an existing legacy `/opt/OSTG/`
real directory if one's present.

### Operator workflow

`Netgen-TrafficGenerator-0.5.10.dmg` / `.AppImage` / `.exe` →
File → Install/Upgrade Server → Fresh Install via SSH → pick
`netgen-server-0.5.10-linux-x86_64.tar.gz`. This is the first
v0.5.x tarball that actually completes a fresh install on an
empty Ubuntu 24.04 host.

### Lessons / the pattern (continued)

This is the SECOND release I've shipped this hour where the bug
was "CI was green, operator was red, because CI was testing
something different from what the operator runs." Per-release
tally so far this session:

* v0.5.6: CI never validated the HTTP-API upgrade path → PEP 668
* v0.5.7: CI false-positively passed because of leftover state
* v0.5.8: CI committed at "now", tested by reading "now"
* v0.5.9: CI mtime was "now", but operator's clock was behind
* v0.5.10: CI smoke exits at require_root before validating layout

The general theme: **CI must validate what the OPERATOR validates,
under the same conditions the operator runs under.** Not "an
approximation of what the operator does." Today's fix: round-trip
now explicitly mirrors `_preflight`'s required-paths check,
without needing root.

## [0.5.9] - 2026-06-07

**Tarball mtimes are now hardcoded to 2020-01-01 UTC. v0.5.8's
SOURCE_DATE_EPOCH approach used git commit time, which was "in the
future" for an operator retrying within minutes of the release.**

Full suite: 1,525 passed, 1 skipped (+1 net new tests, replaces
SOURCE_DATE_EPOCH-pinning test).

### Operator-reported, same release-day, third occurrence

san-hp-srv06, retrying with old v0.5.7 client + new v0.5.8 tarball
within ~minutes of the v0.5.8 release being cut:

```
tar: python-runtime/lib/python3.10/__pycache__/traceback.cpython-310.pyc:
     time stamp 2026-06-07 08:16:04 is 8.172710372 s in the future
tar: python-runtime/lib/python3.10/site-packages/pip/_vendor/rich/traceback.py:
     time stamp 2026-06-07 08:16:04 is 8.047231638 s in the future
... [installer exit rc=3]
```

The v0.5.8 fix used SOURCE_DATE_EPOCH = git commit time. That's the
reproducible-builds standard convention, but it has a fatal flaw
for our threat model: **the commit time IS "now" at release-cut
time.** An operator whose host clock is drifted seconds behind UTC,
retrying within minutes of the release, STILL sees the mtimes as
in the future.

### Fix

`.github/workflows/build-server-tarball.yml` swaps to a hardcoded
past mtime:

```bash
# 2020-01-01 00:00:00 UTC — pre-pandemic, clearly stable,
# no modern host clock can be behind this unless catastrophically
# misconfigured (BIOS battery dead AND no NTP).
STABLE_MTIME=1577836800
tar -czf "$OUT" \
  --owner=root:0 --group=root:0 \
  --mtime="@${STABLE_MTIME}" \
  --sort=name \
  "$ROOT"
```

`SOURCE_DATE_EPOCH` is still exported for any pip/setuptools tooling
that consumes it. Tar just doesn't use it anymore.

### Trade-off

We lose per-commit reproducibility of tar bytes (every v0.5.9+
tarball has identical mtimes regardless of which commit produced
it). We gain unconditional clock-skew safety — which is what
operators actually care about.

Reproducibility was a side benefit; not-failing-to-install is the
primary goal. SOURCE_DATE_EPOCH was the wrong tool for THIS job,
even though it's the right tool for "produce byte-identical
artifacts across CI runs."

### Lessons / what I got wrong

I noted in the v0.5.8 release notes that the SOURCE_DATE_EPOCH
approach could fail for very-recent commits. I should have shipped
the hardcoded-past-mtime fix in v0.5.8 itself, not as a v0.5.9
follow-up. The operator hit the exact failure mode I'd already
identified in CHANGELOG prose.

The bias I want to remember: **"this is borderline" is not a
reason to ship a borderline fix.** If the failure mode is identified,
fix it now, not next release.

### Operator workflow for srv06 right now

If srv06's host clock is still drifted, two paths to unstick today:

```bash
# Fastest — fix the clock once:
ssh root@san-hp-srv06 'timedatectl set-ntp true; \
  systemctl restart systemd-timesyncd; sleep 8; date -u'
```

Then retry with whatever client/tarball is already cached. OR:
download the v0.5.9 client (which has the v0.5.8 `--warning=no-
timestamp` flag AND the v0.5.9 hardcoded-past-mtime tarball), and
the install Just Works regardless of clock drift.

## [0.5.8] - 2026-06-07

**Tarball install no longer fails on hosts with NTP drift. v0.5.7
worked on a freshly-NTP'd host but blew up on srv06 because the
host clock was ~15 s behind UTC and tar refused to extract files
"from the future".**

Full suite: 1,524 passed, 1 skipped (+6 new tests).

### Operator-reported

san-hp-srv06 retrying fresh install with the (now-relocatable)
v0.5.7 tarball:

```
[client] sftp put netgen-server-0.5.7-linux-x86_64.tar.gz
[client] spawn: sudo tar --strip-components=1 -xzf ...; ...
tar: bin/netgen-install: time stamp 2026-06-07 08:06:33
     is 12.518725772 s in the future
tar: bin/netgen-upgrade: time stamp 2026-06-07 08:06:33
     is 12.518893021 s in the future
... (one warning per file in the 19,000-file tarball) ...
[client] installer exit rc=3
```

### Root cause

GNU tar emits a warning AND exits non-zero on future-timestamp
files. The install dialog's spawn script uses `set -e`, so a
single future-mtime warning aborts the install before `mv` or
`netgen-install` ever run.

srv06's `systemd-timesyncd` had drifted ~15 s behind UTC. The
v0.5.7 tarball was packed with "now" mtimes (CI runner's clock,
NTP-accurate to ms). To srv06's tar, every file looked seconds in
the future → cascade of warnings → non-zero exit → install died.

This is **a class of bug, not a one-off** — any operator's host
with NTP drift, frozen-clock VMs, suspended laptops, recently-
restored snapshots, etc. would hit the same trap on any v0.5.7
artifact.

### Fix — two-pronged, mirroring v0.5.7's split

**CI workflow — bake deterministic past mtime into every header:**

```bash
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
tar -czf "$OUT" \
  --owner=root:0 --group=root:0 \
  --mtime="@${SOURCE_DATE_EPOCH}" \
  --sort=name \
  "$ROOT"
```

`SOURCE_DATE_EPOCH` is the reproducible-builds convention. Using
it here gives the tarball two properties at once:

* Stable past mtime per git ref → no host's clock can be "behind"
  it unless mis-set by years
* Reproducible bytes → same tag → identical tarball → operator-
  side verification is feasible

`--sort=name` pairs with `--mtime` for byte-level reproducibility
(without it, readdir ordering varies and the bytes wouldn't match
across CI runs).

**Client install_server_dialog.py — belt-and-braces:**

```python
f"sudo tar --warning=no-timestamp --strip-components=1 ..."
```

Suppresses GNU tar's future-timestamp warning AND its non-zero
exit. This protects operators using a v0.5.8+ client to install
OLDER tarballs (still packed with "now" mtimes). They get the
v0.5.8 client benefit immediately without having to wait for a
new server tarball.

### Verification

Regression tests in `tests/test_v058_tarball_clock_skew.py` pin:

* Workflow sets `SOURCE_DATE_EPOCH` from `git log -1 --pretty=%ct`
* Pack tar passes `--mtime=@${SOURCE_DATE_EPOCH}` AND `--sort=name`
* Client extract passes `--warning=no-timestamp`
* CHANGELOG documents the fix (so operators searching for the
  symptom find this release)

### Operator workflow for srv06 right now

The v0.5.7 client's tar invocation lacks `--warning=no-timestamp`
and the v0.5.7 tarball has CI-runner mtimes — both v0.5.7
artifacts are affected. Three options to unstick:

**Option A — fix the clock once, install with existing v0.5.7:**

```bash
ssh root@san-hp-srv06 'timedatectl set-ntp true; \
  systemctl restart systemd-timesyncd; sleep 5; date -u'
```

Then retry Fresh Install in the v0.5.7 client.

**Option B — upgrade to v0.5.8 client + v0.5.8 tarball:**

Download the v0.5.8 client (`.dmg` / `.AppImage` / `.exe`),
which has `--warning=no-timestamp` baked in AND ships the
SOURCE_DATE_EPOCH-mtime v0.5.8 tarball. Either fix alone is
sufficient; both together = belt-and-braces.

**Option C — manual extract on srv06 once:**

```bash
ssh root@san-hp-srv06
cd /tmp/netgen_install
sudo rm -rf /opt/netgen-server.new /opt/netgen-server
sudo mkdir -p /opt/netgen-server
sudo tar --warning=no-timestamp --strip-components=1 \
  -xzf netgen-server-0.5.7-linux-x86_64.tar.gz \
  -C /opt/netgen-server
sudo /opt/netgen-server/bin/netgen-install
```

### Lessons / pattern

Third release-in-a-row where the same general class of bug bit
us:

* v0.5.6: missing PEP 668 detection in HTTP upgrade endpoint
* v0.5.7: venv built with CI-runner absolute paths
* v0.5.8: tarball mtimes match CI-runner clock, not operator's

The underlying pattern: **artifacts that work on the CI runner
because the CI runner is in a known-good state.** The fix in each
case has been to harden the artifact so it works on *any*
operator state — broken pip flag, weird install path, drifted
clock.

The general rule:

> The artifact must be self-sufficient at the operator's host.
> Anything baked in from the build environment (paths, clocks,
> assumed system state) eventually trips on a host that doesn't
> match. Bake in DETERMINISTIC values, not "whatever was true at
> build time".

Applied:

* `--mtime=$SOURCE_DATE_EPOCH` (clock-independent, this release)
* `pyvenv.cfg home = /opt/netgen-server/...` (path-independent, v0.5.7)
* `--break-system-packages` detection (distro-independent, v0.5.6)
* Round-trip extract test deletes staging tree (CI-state-independent, v0.5.7)

## [0.5.7] - 2026-06-07

**Tarball venv is now actually relocatable. The v0.5.6 fresh-install
broke at the operator's first `sudo /opt/netgen-server/bin/netgen-
install`. v0.5.7 makes the round-trip CI test catch this — and adds
the venv relocation step that should have been there since v0.5.0.**

Full suite: 1,517 passed, 1 skipped (+6 new tests).

### Operator-reported

san-hp-srv06, attempting v0.5.5 → v0.5.6 fresh install via the GUI's
Install/Upgrade Server → Fresh Install via SSH tab:

```
[client] sftp put netgen-server-0.5.6-linux-x86_64.tar.gz
[client] spawn: sudo tar -xzf ...; sudo mv .new /opt/netgen-server;
                sudo /opt/netgen-server/bin/netgen-install
sudo: unable to execute /opt/netgen-server/bin/netgen-install:
      No such file or directory
[client] installer exit rc=1
```

The script file existed and was executable. The "No such file or
directory" comes from Linux's classic mis-reporting: when a script's
shebang interpreter is missing, the kernel reports the script itself
as missing.

### Root cause

`python -m venv` writes ABSOLUTE paths into two places:

1. `netgen-venv/pyvenv.cfg`:
   ```
   home = /home/runner/work/netgen/netgen/netgen-server-0.5.6/python-runtime/bin
   ```
   Python at startup reads `home` to compute `sys.prefix` →
   site-packages location. Wrong `home` → broken imports.

2. `netgen-venv/bin/python3` symlink target:
   ```
   /home/runner/work/netgen/netgen/netgen-server-0.5.6/python-runtime/bin/python3
   ```
   `netgen-install` shebang: `#!/opt/netgen-server/netgen-venv/bin/python`
   → resolves to `bin/python3` symlink → CI-runner path that doesn't
   exist on srv06 → kernel reports "No such file or directory".

Both end up pointing at the CI runner's filesystem. The v0.5.6
round-trip CI test passed despite this — it extracted to
`/tmp/tarball-roundtrip/` while the staging tree was still on disk,
so the absolute symlinks resolved through the LEFTOVER state.
False-positive.

### Fix

**`.github/workflows/build-server-tarball.yml` — venv relocation step
after `python -m venv` (new step 2b):**

```bash
FINAL_PREFIX="/opt/netgen-server"
# 1. Rewrite pyvenv.cfg home to the documented install prefix.
sed -i "s|^home = .*|home = ${FINAL_PREFIX}/python-runtime/bin|" \
  netgen-venv/pyvenv.cfg
# 2. Replace absolute bin/python3 symlinks with relative ones —
#    portable across any extract location.
for link_name in python3 python3.10 python; do
  link=netgen-venv/bin/$link_name
  [ -L "$link" ] || continue
  target=$(readlink "$link")
  case "$target" in
    /*)  rm "$link"
         ln -s ../../python-runtime/bin/python3 "$link" ;;
  esac
done
# 3. Verify no absolute symlinks remain in netgen-venv/bin/.
```

**Strengthened round-trip test:**

* `rm -rf` the staging tree BEFORE extracting. Without this,
  absolute symlinks resolve through leftover state. v0.5.6's
  false-positive ships from exactly this.
* Extract to `/opt/netgen-server` (matching the baked
  pyvenv.cfg). Any other path invalidates the bake-in.
* Actually `exec` `bin/netgen-install` and check exit code +
  output. rc=127 or "no such file" output now FAILS the build
  — the exact srv06 signature.

**Regression tests** (`tests/test_v057_tarball_venv_relocatable.py`)
pin all four contract points so a future workflow refactor surfaces
here.

### Operator workflow for srv06 right now

The failed v0.5.6 fresh-install left `/opt/netgen-server/` half-built
on srv06, but the v0.5.5 system-pip install is still serving (the
new systemd unit was never written because netgen-install never ran).
To get unstuck:

```bash
ssh root@san-hp-srv06 'rm -rf /opt/netgen-server*'
```

Then in the v0.5.7 client → File → Install/Upgrade Server → Fresh
Install via SSH → pick `netgen-server-0.5.7-linux-x86_64.tar.gz` →
Install. This time the venv is portable, the script's shebang
chain resolves, and the install completes cleanly.

### Lessons / pattern

This is the second time in three releases (v0.5.6, v0.5.7) where a
CI test passed because of *something on the CI runner's filesystem
that isn't on the operator's host*. The general rule that emerges:

> CI round-trip tests must DELETE every input source before
> extracting the artifact. If the test still passes, the artifact
> is truly self-contained.

Codified in the v0.5.7 workflow comment.

## [0.5.6] - 2026-06-07

**HTTP wheel-upgrade now works on PEP 668 hosts AND on v0.5.0+
tarball installs.**

Full suite: 1,511 passed, 1 skipped (+5 new tests).

### Operator-reported

san-hp-srv06 (Ubuntu 24.04):

```
[client] POST http://san-hp-srv06:5050/api/admin/upgrade_wheel
[upgrade] cmd: /usr/bin/python3 -m pip install --upgrade ...
error: externally-managed-environment
× This environment is externally managed
[client] pip exited rc=1; aborting
```

Same operator-trust failure class we whacked through v0.4.7 /
v0.4.8 / v0.4.9. v0.5.1's SshUpgradeWorker fix covered the SSH
upgrade path (client → SSH → systemctl restart) but missed the
HTTP API endpoint — different code path, same bug class.

### Fix

Two-branch dispatch in `/api/admin/upgrade_wheel`:

**Branch 1 — v0.5.0+ tarball install.** If
`/opt/netgen-server/bin/netgen-upgrade` exists (executable),
dispatch through it. The script runs pip in the bundled venv
(zero PEP 668 surface — venvs are exempt) AND handles
`systemctl restart` inside itself.

**Branch 2 — v0.4.x system install.** Use `sys.executable -m
pip install` as before, but conditionally add
`--break-system-packages` based on detection of
`/usr/lib/python3*/EXTERNALLY-MANAGED` (the canonical PEP 668
signal). Detection cached on the module so repeat upgrades
don't re-stat the filesystem.

Pre-PEP 668 hosts (Ubuntu 22.04 Jammy, etc.) have neither the
marker NOR `--break-system-packages` support in their older
pip — conditional detection is the only correct shape.

Log lines record which branch ran:

```
[upgrade] cmd: ...
[upgrade] mode: tarball:netgen-upgrade
```

or

```
[upgrade] mode: legacy:system-pip+break-system-packages
```

so when an operator-reported failure lands, the first line of
`/var/log/netgen-upgrade.log` reveals which dispatch fired.

### Tests

`tests/test_v056_admin_upgrade_pep668.py` — 5 tests pinning:

  * Endpoint checks `/opt/netgen-server/bin/netgen-upgrade`
    with both `isfile` AND `access(X_OK)` (avoids dispatching
    to a stale empty file)
  * Legacy path detects `EXTERNALLY-MANAGED` marker
  * `--break-system-packages` added ONLY when detected
  * Detection cached at module level
  * Both branches log `upgrade_mode` for diagnostic clarity

### Operator action

Upgrade srv06 (and any other host that hit this) to v0.5.6.
The v0.5.6 wheel can be installed via the same Upgrade Server
flow — the dispatch will detect EXTERNALLY-MANAGED on srv06,
add the flag, and the install will succeed THIS time. On the
next upgrade after that, the same dispatch keeps working.

## [0.5.5] - 2026-06-07

**Cleaner interface context-menu labels.**

Full suite: 1,506 passed, 1 skipped.

### Change

Right-click on an interface in the server tree showed:

```
Set enp181s0f0np0 Online
Set enp181s0f0np0 Offline
```

The interface name is redundant — the operator already sees it
in the row they right-clicked on. Repeating it inside the menu
item is just noise.

v0.5.5 simplifies to:

```
Online
Offline
```

The tooltip on each item still includes the full target so the
operator can hover-confirm before clicking:

  `ip link set enp181s0f0np0 up via POST /api/interfaces/...`

Server-side endpoint (`POST /api/interfaces/<iface>/admin`) and
all the v0.5.4 safety / handler / confirmation logic are
unchanged.

### Tests

`tests/test_v054_interface_admin_context_menu.py::test_context_
menu_offers_both_up_and_down` updated to pin the clean label
shape (`QAction("Online", ...)` instead of the verbose form) +
forbid the v0.5.4 verbose form so a refactor that re-adds the
prefix surfaces here.

## [0.5.4] - 2026-06-07

**Right-click an interface in the TGEN list to take it online /
offline.**

Full suite: 1,506 passed, 1 skipped (+11 new tests).

### Operator request

Bring an interface up or down without dropping to a shell. Same
HTTP-first principle as the v0.5.2 reboot and v0.5.3
restart-service work: the server already runs as root, so the
client can hand off via REST.

### Changes

**1. New `POST /api/interfaces/<iface>/admin`.**

```
POST /api/interfaces/enp181s0f0np0/admin
{"state": "down"}     →  200 {"ok":true, "operstate":"down"}
                         400 if state isn't "up" / "down"
                         404 if /sys/class/net/<iface> doesn't exist
```

Safety:

  * `iface` validated via `/sys/class/net/<iface>` — operator-
    controlled URL component can't reach `ip link set` unless
    the kernel already knows about it.
  * `state` strict whitelist of exactly `"up"` or `"down"`. No
    free-form `set <iface> <whatever>` paths.
  * `ip link set -- <iface> <state>` (double-dash) so an
    interface starting with `-` can't be parsed as an option.
  * Response includes kernel-observed `operstate` so the
    operator sees what ACTUALLY happened (e.g. requested up,
    got DORMANT because the link isn't connected) — not just
    an echo of the request.

**2. Server-tree right-click context menu.**

Right-click on an **interface** item (child of a TG node) opens:

```
Set <iface> Online
Set <iface> Offline
```

Right-click on a server-level row does nothing — keeps the
existing left-click flows untouched.

  * Set Offline → confirmation dialog (`QMessageBox.warning`)
    because it stops any streams using the interface.
  * Set Online → no confirmation (idempotent, safe; extra
    dialog would slow down post-maintenance recovery).
  * After success: the tree refreshes automatically so the
    operator sees the new state without re-clicking the
    chassis.
  * On HTTP 404 (pre-v0.5.4 server): clear "upgrade the server
    to use Set Online / Offline" hint — actionable, not
    confusing.

### Tests

`tests/test_v054_interface_admin_context_menu.py` — 11 tests
pinning:

  * Endpoint registered + sysfs validation + strict state
    whitelist + double-dash argv + operstate in response
  * `server_tree.setContextMenuPolicy(CustomContextMenu)` +
    signal wiring
  * Handler returns when `parent is None` (skips server rows)
  * Both Online + Offline actions present
  * Offline requires confirmation; Online does not
  * 404 surfaces a v0.5.4 upgrade hint
  * Success refreshes the tree

### Operator action

Upgrade servers to v0.5.4+ via the wheel-upgrade path. After
upgrade, right-click on any interface in the server tree to
get the Online / Offline options. Pre-v0.5.4 servers respond
404 and the client surfaces a clear upgrade hint — no silent
failures.

## [0.5.3] - 2026-06-07

**Restart TGEN Service + Reboot Physical Server moved from
the Server menu to the Add TGEN Chassis dialog. Both run
via HTTP (no SSH).**

Full suite: 1,495 passed, 1 skipped (+10 new tests).

### Operator-stated rationale

The Add TGEN Chassis dialog already shows per-chassis health
(LED, version, health column — v0.2.33 / v0.2.34). Operators
looking at chassis state are the same people who want to
restart or reboot — keeping those actions in a global Server
menu meant a context jump (select in tree → open menu → click)
when a single dialog could do both.

### Changes

**1. New `POST /api/system/restart_service` endpoint.**

Mirrors the v0.5.2 `/api/system/reboot` design:

  * `Popen` (not `run`) so the HTTP 200 returns BEFORE the
    `systemctl restart` kills the Flask thread.
  * Default 2-second delay; clamped to `[1, 30]`.
  * Tries `netgen-server` first, falls back to legacy
    `ostg-server` so unmigrated hosts work too.

**2. AddTGenDialog gains two buttons.**

```
[Connect Selected] [Open admin] [Remove from history] [Test all]
[Restart TGEN Service] [Reboot Physical Server]
```

Each button reads the selected history-table row's entry,
composes `<scheme>://<addr>:<port>/api/system/...`, and posts.

  * HTTP 200 → status line shows `✓ Restart scheduled` /
    `✓ Reboot scheduled in 5s. Wait 3-5 minutes...`.
  * HTTP 404 → `⚠ <host> is on a pre-v0.5.3 server (no
    /api/system/restart_service). Upgrade...` — actionable.
  * No row selected → `Pick a chassis row first, then click
    Restart.` — friendly, not a silent no-op.

**Reboot uses `QMessageBox.warning` (not `.question`)** + the
3-5 minute downtime message so operators have appropriate
friction before confirming the destructive action. Restart
uses the lighter `.question` style.

**3. Server menu cleanup.**

The two `QAction` entries are removed from `main.py`'s
Server-menu setup. Add / Remove TGEN Chassis stay (v0.4.9
contract preserved). New Server menu shape:

```
Server
├── Add TGEN Chassis...       (Ctrl+N)
├── Remove TGEN Chassis
├── ─────────────
├── Make Selected Servers Online
└── Mark Selected Servers Offline
```

The backing methods `self.restart_server` and
`self.reboot_server` remain in `menu_actions.py` — any
operator script that hooked them keeps working. Only the
menu entries are gone.

### Tests

`tests/test_v053_restart_reboot_in_dialog.py` — 10 tests
pinning:

  * Endpoint exists; uses `Popen` + sleep; tries netgen-server
    AND ostg-server unit names
  * Dialog has `restart_btn` + `reboot_btn`; both wired to
    handlers
  * Handlers POST the right endpoint; surface 404 with the
    appropriate upgrade-hint version (v0.5.3 for restart,
    v0.5.2 for reboot)
  * Reboot uses `QMessageBox.warning` + mentions the 3-5
    minute downtime
  * Both handlers show a friendly hint when no row is selected
  * Server menu no longer constructs the two QActions
  * Add / Remove TGEN Chassis stay in the Server menu (v0.4.9
    contract intact)

### Operator action

Upgrade servers to v0.5.3+ via the wheel-upgrade path. After
upgrade, the Add TGEN Chassis dialog buttons work directly
(HTTP, no SSH). Pre-v0.5.3 servers respond with HTTP 404 and
the dialog surfaces a clear "upgrade to v0.5.3+" hint — no
silent failures.

## [0.5.2] - 2026-06-07

**Server → Reboot Physical Server actually reboots the server now.**

Full suite: 1,485 passed, 1 skipped (+9 new tests).

### Operator-reported

Server menu → Reboot Physical Server showed
`✅ Reboot initiated successfully` but no reboot happened. Silent
failure — operators kept clicking, kept getting ✅, server kept
running.

### Root cause

`traffic_client/menu_actions.py:_reboot_servers_list` used:

```python
cmd = ["ssh", f"root@{hostname}", "reboot"]
result = subprocess.run(cmd, capture_output=True, ...)
if result.returncode == 0 or result.returncode == 255:
    results.append("✅ Reboot initiated successfully")
```

Two compounding bugs:

  1. **Passwordless `ssh root@host` rarely works** in production
     — operators don't have root SSH keys distributed. SSH falls
     back to a password prompt, hits `subprocess.run`'s captured
     stdin → exits with rc=255 "Permission denied".
  2. **The code treated rc=255 as SUCCESS.** Original rationale was
     "SSH disconnects during reboot is expected" — true for the
     happy path, but rc=255 also catches "Permission denied" /
     "Host key verification failed" / "Connection refused" — the
     EXACT cases where no reboot happened. Operator sees ✅;
     reality is ✗.

### Fix — two halves

**Server side: new `POST /api/system/reboot`.**

The server already runs as root (per its systemd unit's
ExecStart) so it can schedule its own reboot via
`subprocess.Popen` — same pattern as the existing
`/api/dpdk/iommu` reboot. No SSH credentials needed on the client.

```python
@app.route("/api/system/reboot", methods=["POST"])
def system_reboot():
    delay_s = max(1, min(60, int(data.get("delay_s", 5))))
    subprocess.Popen(
        ["sh", "-c",
         f"sleep {delay_s} && "
         f"(systemctl reboot 2>/dev/null || /sbin/reboot || reboot)"],
        stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, close_fds=True,
    )
    return jsonify({"ok": True, "delay_s": delay_s,
                    "reboot_at_unix": int(time()) + delay_s,
                    "hostname": socket.gethostname()})
```

- `Popen` (not `run`) so the response reaches the client BEFORE
  /sbin/reboot tears the network down.
- 5-second default delay → HTTP 200 has time to land.
- `delay_s` clamped to `[1, 60]` so a malicious `delay_s=99999`
  request can't silently DoS the operator.
- Three-fallback reboot command (`systemctl reboot` →
  `/sbin/reboot` → bare `reboot`) covers systemd / non-systemd /
  container hosts.

**Client side: HTTP first, SSH only on 404.**

```python
r = requests.post(f"{api_base}/api/system/reboot",
                  json={"delay_s": 5}, timeout=5)
if r.status_code == 200:
    results.append(f"✅ TG {tg_id}: Reboot scheduled in {delay}s (HTTP API)")
    continue
elif r.status_code == 404:
    # pre-v0.5.1 server — fall through to SSH path
    ...
```

The HTTP 200 IS the proof — endpoint only returns after `Popen`
succeeds. No "success vs disconnected" ambiguity.

The SSH fallback (for pre-v0.5.1 hosts) is now honest:

  - Adds `ssh -o BatchMode=yes -o ConnectTimeout=5` so SSH
    fails-fast on password prompts instead of hanging.
  - Parses stderr for hard-failure markers (`permission denied`,
    `host key verification failed`, `connection refused`, etc.).
    rc=255 + hard-fail marker → reported as ✗, not ✓.
  - rc=255 WITHOUT a hard-fail marker is the genuine "SSH
    disconnected mid-reboot" case and stays a ✓.
  - When SSH genuinely fails, the message includes
    "Upgrade the server to v0.5.1+ to use the HTTP reboot
    endpoint (no SSH required)" — actionable, not just ✗.

### Tests

`tests/test_v052_reboot_endpoint.py` — 9 tests pinning:

  - Endpoint exists at `POST /api/system/reboot`
  - Uses `Popen` (not blocking `subprocess.run`)
  - Returns `ok:true` + `delay_s` in body
  - Clamps `delay_s` with a numeric ceiling
  - Three-fallback reboot command shape
  - Client calls HTTP endpoint BEFORE SSH fallback
  - Client falls back to SSH ONLY on 404 (not on 500 / network)
  - SSH stderr check includes `permission denied` + other hard-
    failure markers
  - SSH command includes `BatchMode=yes`

### Operator action

Upgrade existing v0.4.x / v0.5.0 servers to v0.5.2 via the v0.5.1
Upgrade tab. Once a server is on v0.5.2+, the Reboot Physical
Server menu works without any SSH-side configuration.

Pre-v0.5.2 servers still work via the (now honest) SSH fallback,
which will surface the actual failure mode instead of silently
reporting ✓.

## [0.5.1] - 2026-06-07

**Install/Upgrade Server dialog: full polish for the v0.5.0
tarball flow. One critical bug fix + four UX cleanups.**

Full suite: 1,476 passed, 1 skipped (+6 new tests).

### Fix 1 — Upgrade now works on v0.5.0 tarball hosts (CRITICAL)

Pre-v0.5.1 the Upgrade tab's shell payload was hardcoded to
`pip3 install --upgrade --force-reinstall --no-deps <wheel>`.
That worked on v0.4.x hosts: system pip3 wrote to
`/usr/lib/python3.X/dist-packages/` and the systemd unit's
ExecStart resolved through there.

On a v0.5.0 tarball host, the systemd unit's ExecStart points at
`/opt/netgen-server/netgen-venv/bin/ostg-server` — a DIFFERENT
Python. System pip3 wrote the wheel to the system Python; the
server kept running the OLD code; the operator never knew the
upgrade had silently failed.

Fix: the shell payload now tests `[ -x /opt/netgen-server/bin/
netgen-upgrade ]` at SSH-execution time:

  * If present (v0.5.0+ host) → dispatch through
    `/opt/netgen-server/bin/netgen-upgrade <wheel>` (the script
    inside the tarball already does pip-in-bundled-venv +
    systemctl restart + /api/health verify).
  * Else (v0.4.x host) → fall back to the legacy `pip3 install`
    path — unchanged.

The test branches with a one-line `[v0.5.0]` / `[v0.4.x]` log so
the operator sees which path actually ran.

### Fix 2 — Install-mode indicator under the file picker

Operator picks a file; pre-v0.5.1 nothing told them which install
path would run. A new live label under the wheel/tarball field
updates as the path changes:

```
Wheel / tarball: [/path/to/netgen-server-0.5.0-linux-x86_64.tar.gz ] [Browse...]
                 → v0.5.0 tarball install (bundled venv, no system pip)
```

vs:

```
Wheel / tarball: [/path/to/ostg_trafficgen-0.5.0-py3-none-any.whl   ] [Browse...]
                 → Legacy wheel install (install_ostg_complete.py path)
```

No more guessing mid-install about which flow is active.

### Fix 3 — DPDK flags no longer silently dropped

`--no-dpdk` / `--skip-dpdk-build` are install_ostg_complete.py-
specific flags. `bin/netgen-install` (inside the tarball) doesn't
recognise them. Pre-v0.5.1 they were passed through and silently
ignored.

v0.5.1: when the operator picks a tarball, the click handler strips
those flags AND logs:

```
[client] --no-dpdk: ignored on tarball install
         (netgen-install handles DPDK via runtime detection)
```

So the checkbox state didn't carry forward AND the operator knows
why.

### Fix 4 — install_ostg_complete.py field disabled for tarball mode

The "Installer:" row is only relevant to legacy wheel installs. The
indicator logic now greys it out + adds a tooltip when a tarball is
picked. Same for the two DPDK flag checkboxes.

### Fix 5 — Fresh Install file picker defaults to tarball filter

Pre-v0.5.1 the file dialog's default filter was always `*.whl`. On
the Fresh Install tab (where tarball is the recommended artifact in
v0.5.0+), `_browse_wheel` now accepts `prefer_tarball=True` and
opens the dialog with the tarball filter selected. Operators on the
Upgrade tab still see `*.whl` as the default.

The wheel-row label also changed from "Wheel:" to "Wheel /
tarball:" so the operator's first glance tells them both extensions
are valid.

### Tests

`tests/test_v051_install_dialog_polish.py` — 6 tests pinning the
shell-dispatch contract (executable test, both branches present),
the indicator method + textChanged wiring, the DPDK flag-strip
logic, the installer-field setEnabled gate, the
`prefer_tarball=True` kwarg, and the updated row label.

### Operator action

Existing v0.5.0 hosts that haven't received a wheel-based upgrade
yet are NOT affected — they were upgraded via the v0.4.x SSH path
(if they were upgraded at all). The v0.5.1 client paired with a
v0.5.0+ server is the working combo for routine upgrades going
forward.

## [0.5.0] - 2026-06-07

**Distribution-ready fresh install via a self-contained server
tarball. No more system-pip / apt-package / PEP-668 surface.**

Full suite: **1,470 passed, 1 skipped** (+85 new tests across 4
new test files).

### Why this release exists

This session shipped three reactive installer fixes:

  * **v0.4.7** — Ubuntu 24.04 PEP 668 (`--break-system-packages`)
  * **v0.4.8** — deps-install silent no-op → `No module named 'flask'`
  * **v0.4.9** — `netcat` virtual-package failure on Noble

Each fix only surfaced AFTER a different server hit it. With wider
distribution starting, that whack-a-mole pattern doesn't scale:
end users don't have CI catching install failures before they bite.

v0.5.0 eliminates the **class** of bugs instead of patching each
one. Operator picks a `.tar.gz` in the Fresh Install dialog;
client uploads ONE file; target extracts to /opt/netgen-server and
runs the bundled `bin/netgen-install`. Zero system pip, zero apt
packages for the Python side, zero virtual-package guesswork.

### Architecture

The new artifact `netgen-server-0.5.0-linux-x86_64.tar.gz`
contains:

```
netgen-server-0.5.0/
├── python-runtime/       Bundled CPython 3.10.14
│                         (from python-build-standalone)
├── netgen-venv/          Pre-built venv with the wheel + all deps
│                         (Flask, Scapy, requests, psutil, ...)
├── share/netgen/         DPDK tx_worker source, Docker (FRR), etc.
├── bin/
│   ├── netgen-install    Pre-flight + systemd + FRR Docker + verify
│   ├── netgen-upgrade    pip install wheel into bundled venv
│   └── netgen-uninstall  Clean removal (+ --purge for full wipe)
├── VERSION
└── README.txt
```

The bundled venv's `pip` is the only pip the target ever runs.
PEP 668's `EXTERNALLY-MANAGED` marker doesn't apply to it (PEP 668
explicitly exempts venvs).

### Operator workflow

**Fresh install (Path A — direct on the server):**

```bash
wget .../v0.5.0/netgen-server-0.5.0-linux-x86_64.tar.gz
sudo mkdir -p /opt/netgen-server
sudo tar --strip-components=1 -xzf netgen-server-0.5.0-linux-x86_64.tar.gz \
    -C /opt/netgen-server
sudo /opt/netgen-server/bin/netgen-install
```

`netgen-install`'s pre-flight gate refuses unsupported distros
with a concrete remediation (Ubuntu 22.04 + 24.04 only without
`--force-install-unsupported`). Failures surface BEFORE state
gets written.

**Fresh install (Path B — from the GUI client):**

Client → File → Install/Upgrade Server → Fresh Install → pick a
`.tar.gz` instead of a `.whl`. The dispatcher auto-detects the
extension and runs the right install path.

**Routine upgrades (wheel-based, unchanged UX, ~30 sec):**

```bash
sudo netgen-upgrade ./ostg_trafficgen-0.5.1-py3-none-any.whl
```

`netgen-upgrade` runs `pip install --force-reinstall` inside the
bundled venv (the v0.4.8 lesson stays — pip must not silently
no-op). Post-upgrade import sanity check catches a bad wheel
before systemd starts crash-looping.

### What this eliminates permanently

| Bug class from v0.4.x | v0.5.0 state |
|---|---|
| PEP 668 / `--break-system-packages` | Gone — bundled venv is exempt |
| `netcat` / `libmlx5-dev` virtual packages | Gone — zero apt deps for the Python side |
| Deps-install silent no-op (v0.4.8) | Gone — deps pre-installed at CI tarball-build time |
| Python-version mismatch (pip3 vs /usr/bin/python3) | Gone — bundled Python is the only one |
| `install_ostg_complete.py` Python-runtime drift | Gone — installer runs INSIDE the bundled venv |

### Implementation phases (all in this release)

**Phase 1a — Tarball build pipeline.** New
`.github/workflows/build-server-tarball.yml`. CI downloads
python-build-standalone, creates a venv against it, pip-installs
the wheel + deps with `--force-reinstall`, packages everything into
a tar.gz with `--owner=root:0 --group=root:0`. Smoke tests:
imports flask + scapy + requests + psutil + run_tgen_server,
round-trip extract + re-verify (catches build-side filesystem
state contamination).

**Phase 2 — In-tarball install scripts.** Three new scripts in
`scripts/tarball/`, copied into the tarball's `bin/` at
build-time with shebangs rewritten to point at the bundled venv's
Python:

  * `netgen-install` (~400 lines): pre-flight checks (OS gate, disk,
    Docker, libpcap-via-Scapy import test), FRR Docker build,
    systemd unit write + restart, `/usr/local/bin/` PATH symlinks,
    `/api/health` verification. Mirrors logs to both stdout AND
    `/var/log/netgen-install.log`. Idempotent on re-install.
  * `netgen-upgrade <wheel>` (~150 lines): pip install
    `--force-reinstall` inside bundled venv, post-upgrade import
    check, systemctl restart, `/api/health` verification.
  * `netgen-uninstall [--purge]` (~130 lines): systemd unit + PATH
    symlinks removed by default; `--purge` also removes the install
    tree, FRR Docker image, and install log. `--purge` has a
    basename-whitelist guard against `--install-root` typos that
    would `rmtree` `/home` or `/etc`.

Venv slim drops `__pycache__`, `*.pyc`, `*.pyo`, `tests/` directories
in site-packages, and `include/`. Saved ~157 MB; remaining
optimization deferred to v0.5.1.

**Phase 3 — GUI dispatcher + release wiring.** `SshInstallWorker`
accepts a new `tarball_path` kwarg. When set, uploads ONE file
(the tarball) and spawns the tar-extract + `bin/netgen-install`
chain — skipping the entire wheel + installer + resources/dpdk +
ostg_docker + Dockerfile.frr upload that the legacy path needed.
The file picker accepts both `.whl` and `.tar.gz`; the click
handler infers install path from extension.

`build-server-tarball.yml` now also triggers on `v0.5.*` /
`v0.[6-9].*` / `v[1-9].*` tag pushes and uses
`softprops/action-gh-release@v2` (gated on
`startsWith(github.ref, 'refs/tags/v')`) to attach the tarball to
the same GH release `release.yml` is appending its wheel / .dmg /
.exe / .AppImage to. `generate_release_notes: false` and
`append_body: false` keep release.yml's CHANGELOG-driven notes
authoritative; the tarball workflow only adds the asset.

### Tests

- `tests/test_v050_server_tarball_workflow.py` (14 tests) —
  workflow pins python-build-standalone version, builds on
  ubuntu-22.04 (older glibc floor → forward-compat), smoke-tests
  critical imports, does round-trip extract verify, copies install
  scripts + rewrites shebangs, slims the venv, preserves the wheel
  alongside the tarball.
- `tests/test_v050_phase3_gui_tarball_dispatch.py` (9 tests) —
  `SshInstallWorker` accepts `tarball_path` with None default;
  tarball branch skips wheel uploads and spawns
  `bin/netgen-install` instead of `install_ostg_complete.py`;
  file picker filter includes `.tar.gz`; validation skips
  installer-path requirement for tarball installs; workflow
  triggers on `v*` tags, attaches via softprops, doesn't clobber
  release.yml's body.

Static-source tests against the three install scripts (existence,
shebang prefix, `SUPPORTED_DISTROS` gate, `--force-reinstall` in
upgrade, `--purge` whitelist).

### Legacy install_ostg_complete.py path

UNTOUCHED in v0.5.0. Operators with v0.4.x systemd units keep
running exactly the same. Migration to v0.5.0 is opt-in: pick a
`.tar.gz` in the Fresh Install dialog instead of a `.whl`. The
v0.4.x → v0.5.0 jump cleans up the old `/usr/local/lib/python3.10/
dist-packages/` install side-by-side with `/opt/netgen-server/`;
a future v0.5.1 migration helper can collapse the two.

### Operator action on v0.5.0+ hosts

Fresh hosts get the tarball path automatically. Existing v0.4.x
hosts can:

  * Stay on the legacy installer (no breakage)
  * OR run a v0.5.0 tarball install — backs up the v0.4.x systemd
    unit to `.service.bak`, writes the new one pointing at
    `/opt/netgen-server/netgen-venv/bin/ostg-server`. Session state
    in `~/Documents/OSTG/session.json` (or wherever
    `_current_session_path` points) is preserved — it's outside
    the install tree.

## [0.4.9] - 2026-06-07

**Two small fixes surfaced by the v0.4.8 fresh-install log: UX
polish on the menu structure and a Ubuntu 24.04 apt-batch failure.**

Full suite: **1,447 passed, 1 skipped** (+9 new tests).

### Fix 1 — Add / Remove TGEN Chassis moved File → Server menu

Operator-reported: kept hunting for "Add TGEN Chassis…" under
the Server menu, since every other TGen-management action (Make
Online / Mark Offline / Restart Service / Reboot Physical)
already lives there. Top-of-File-menu was a leftover from when
the app had only "Add Chassis" + "Save Session" and the
distinction didn't matter.

Moved both actions (plus the Ctrl+N shortcut) to the top of the
Server menu, with a separator between them and the online-state
actions. New Server menu shape:

```
Server
├── Add TGEN Chassis...  (Ctrl+N)   ← was under File
├── Remove TGEN Chassis              ← was under File
├── ─────────────
├── Make Selected Servers Online
├── Mark Selected Servers Offline
├── ─────────────
├── Restart TGEN Service...
└── Reboot Physical Server...
```

Pinned by `tests/test_v049_tgen_actions_under_server_menu.py`
(6 tests): both QActions constructed inside the Server-menu
source block, File menu has zero references, Ctrl+N stays
adjacent to the action, ordering is Add → Remove → separator →
online-state actions.

### Fix 2 — `netcat` virtual-package failure on Ubuntu 24.04

From the v0.4.8 fresh-install log on a clean Noble box:

```
E: Package 'netcat' has no installation candidate
[WARNING] Package installation encountered issues:
   ... returned non-zero exit status 100.
```

On Ubuntu 24.04 (Noble) `netcat` is a **virtual** package —
provided by `netcat-openbsd` or `netcat-traditional` with no
default. apt's batch install bails with rc=100 on the missing
candidate, killing the whole 60-package system-deps install.

The installer continued (failure here is correctly downgraded to
WARNING since netgen doesn't strictly need every userspace tool),
but the operator-facing log was confusing and some tools were
silently missing.

`install_ostg_complete.py:_install_apt_packages` already had this
right in the apk (Alpine) branch with `netcat-openbsd`. v0.4.9
aligns the apt branch. The zypper (openSUSE) branch's bare
`netcat` stays — SUSE has a real package by that name, distinct
from the Debian-family virtual-package split.

Pinned by `tests/test_v049_netcat_package_noble.py` (3 tests):
apt branch uses `netcat-openbsd`, apk branch keeps
`netcat-openbsd`, zypper branch keeps bare `netcat`.

### Operator action

GUI Fresh Install on v0.4.9 will run a clean system-deps apt batch
(no rc=100 warning) and the Add/Remove TGen actions land where
operators look for them.

## [0.4.8] - 2026-06-07

**Fresh install on a clean host now actually installs the wheel's
dependencies. No more silent crash-loops.**

Operator-reported on san-hp-srv06 (fresh install on a clean Ubuntu
24.04 host via the GUI client's Fresh Install tab):

```
netgen-server.service: Scheduled restart job, restart counter is at 743.
[ostg-server] Failed to import run_tgen_server: No module named 'flask'
netgen-server.service: Main process exited, code=exited,
                       status=2/INVALIDARGUMENT
```

The installer reported success. The wheel was on disk. But Flask +
scapy + requests weren't installed, so systemd crash-looped the
server 743+ times trying to import a Flask-based module.

### Root cause

Pre-v0.4.8 the install was a two-pass strategy:

```python
# Step 1 — wheel artifact only
pip3 install --break-system-packages --force-reinstall --no-deps <wheel>

# Step 2 — deps "if needed" (THE BUG)
pip3 install --break-system-packages --upgrade-strategy only-if-needed <wheel>
```

Step 2 was supposed to install missing deps. But with the wheel
already current (step 1 just installed it), pip's `--upgrade-strategy
only-if-needed` can decide *"package is at target version, nothing
to do"* and skip dependency resolution entirely. Flask + scapy +
requests never land.

Worse: pre-v0.4.8 step 2 failure was logged as a non-fatal
`WARNING`. The installer printed `Netgen installation completed
successfully!` while the server was about to crash-loop.

### Fix

Three changes to `install_ostg_complete.py:install_ostg()`:

1. **Deps pass uses `--force-reinstall`** (not `--upgrade-strategy
   only-if-needed`). This guarantees pip re-resolves the full
   dependency graph. Already-installed deps at the right version
   are no-ops at the install layer, so the speed hit on a fresh
   host is negligible.
2. **Deps-pass failure is now FATAL.** `raise SystemExit(1)` with
   an explicit message hinting at the symptom (`No module named
   'flask'`) so the operator doesn't have to dig through systemd
   logs to understand what went wrong.
3. **Post-install sanity check.** After both pip passes succeed,
   the installer runs `python3 -c "import flask, scapy, requests"`
   on the target. If the imports fail (deps missing, or python-
   version mismatch between `pip3` and `/usr/bin/python3`), the
   install fails loudly — better to surface during setup than to
   ship a crash-looping service.

Same PEP 668 belt-and-suspenders retry (added in v0.4.7 to the
wheel install) now applies to the deps pass too.

### Tests

`tests/test_v048_installer_deps_resolution.py` — 6 tests pinning:

- Deps pass uses `--force-reinstall` (not the broken `--upgrade-
  strategy only-if-needed`)
- Deps-pass failure is FATAL (raises `SystemExit`)
- Same PEP 668 retry safety net on the deps pass
- Post-install sanity check exists with `python3 -c "import flask"`
- The check runs AFTER deps install (correct ordering)
- The check failure is also FATAL
- Both pip-install commands are present in the right shape
  (regression for refactors that drop one half)

Full suite: 1,438 passed, 1 skipped.

### Operator action

Existing v0.4.7 hosts that DID get a working install need no
action — they have Flask installed. Fresh installs on v0.4.7 that
ended up in the crash-loop state (san-hp-srv06 was one) need
either v0.4.8's installer OR the one-time manual:

```bash
ssh root@<host> 'pip3 install --break-system-packages --force-reinstall \
    /tmp/netgen_install/ostg_trafficgen-0.4.7-py3-none-any.whl && \
  systemctl restart netgen-server'
```

The v0.4.8 GUI client's Fresh Install tab will run the fixed
installer end-to-end.

## [0.4.7] - 2026-06-06

**Seven operator-reported bugs + four feature gaps. Combined ship
of what was internally v0.4.6 + v0.4.7.**

Full suite: **1,432 passed, 1 skipped** (+46 new tests).

---

### Fix 1 — TX interface no longer mirrors RX columns (was v0.4.6)

Operator-reported on svl-d-ai-srv04 with v0.4.5 installed: the
Interface Statistics table showed the TX iface `enp160s0f0np0`
with `Received Frames = 3,836`, `Receive Frame Rate = 397.36 fps`,
and `Receive Bit Rate = 203.45 Kbps` — exact mirrors of the RX
iface `enp181s0f0np0`. The TX iface didn't actually receive those
packets; the bug was in the client-side aggregator
`traffic_client/statistics_section.py:1161-1165`:

```python
# Pre-fix
if flow_tracking:
    merged_statistics[tx_iface]["rx"] += rx           # bug
    merged_statistics[tx_iface]["received_bytes"] += rx * frame_size
    merged_statistics[tx_iface]["receive_fps"] += rx_rate
    merged_statistics[tx_iface]["receive_bps"] += rx_rate * frame_size * 8
```

The RX-aggregation block already attributed RX to the RX iface
correctly — the TX-aggregation block was duplicating it onto the
wrong interface. For loopback tests (TX iface == RX iface) the
same lines double-counted RX (1,960 instead of 980 on a 1000-tx
stream). Fix: delete the four mirror-lines. The RX-iface bucket is
the sole place RX gets attributed.

Pinned by `tests/test_v046_tx_iface_rx_mirror.py` (5 tests).

### Fix 2 — DPDK readiness chip no longer freezes the UI

Operator-reported: "dpdk check is slow when moving selection from
one TG to another TG". Root cause: `DpdkReadinessChip.refresh()`
did a synchronous `requests.get(..., timeout=5)` on the UI thread.
Every poll (or selection-change) could block the Qt event loop for
up to 5 sec when the server was sluggish.

Refactored to async via a one-shot `QThread` worker
(`_DpdkStatusFetchThread`); the chip emits a signal back to the UI
thread when the payload arrives. Three additional improvements:

- **Dedup guard** `_fetch_in_flight` — rapid TG-clicks (4 in 2 sec)
  coalesce to ONE in-flight fetch, not four.
- **Selection-change kick** — `_on_server_selection_changed_combined`
  in `traffic_client/server_section.py` now calls `chip.refresh()` so
  the chip switches state instantly when the TG changes, instead of
  waiting up to 30 sec for the next poll.
- **`stop()` waits the worker** (bounded 2 sec) so shutdown doesn't
  trip Qt's "QThread destroyed while still running" abort.

Pinned by `tests/test_v047_dpdk_chip_async.py` (7 tests).

### Fix 3 — Session-file path persists across restarts

Operator-reported: opened the client, expected streams + TGs from
prior work, got "TG 0" with no streams and no other TGs. Root
cause: `_current_session_path` was set when Save As / Load From…
ran, but never persisted. On restart, `main.py:81` always reset it
to the default `~/Documents/OSTG/session.json` (empty for first-
time macOS-app users), so `load_session()` loaded that and the
auto-add path tacked on a localhost TG 0.

Fix: persist the path to `QSettings` whenever Save As / Load From
changes it; on startup, read it back. Fall back to the default if
the persisted file no longer exists (moved/deleted → no wedge).

Pinned by `tests/test_v047_session_path_persistence.py` (6 tests).

### Fix 4 — Removing one TGen no longer wipes others' connection status

Operator-reported on the AddTGenDialog: had 3 chassis in the
history table, all probed (✓ LEDs, version + health populated),
clicked Remove on one — the other two reverted to "?" gray,
version "?", health "—". Looked like the other chassis lost their
connection.

Root cause: `_remove_selected_from_history` called
`_populate_history_table()` after deleting one entry. That helper
`setRowCount(0)`s and rebuilds every row with default `"?"`
placeholders — the LED / version / health items were never in
`self._entries`, only in the QTableWidget items.

Fix: surgical `self.table.removeRow(i)`. Qt shifts subsequent rows
up by one and leaves their items untouched.

Pinned by `tests/test_v047_add_tgen_remove_preserves_status.py` (3
tests).

### Fix 5 — Fresh install on Ubuntu 24.04+ (PEP 668)

Operator-reported on a fresh Noble VM:

```
[ERROR] Wheel install failed: error: externally-managed-environment
× This environment is externally managed
╰─> To install Python packages system-wide, try apt install...
hint: See PEP 668 for the detailed specification.
[client] installer exit rc=1
```

Ubuntu 24.04+ ships `/usr/lib/python3*/EXTERNALLY-MANAGED` (PEP
668), and the system pip refuses every `pip3 install` without
`--break-system-packages`. The installer had 4 such callsites,
none passing the flag.

Fix: new `_detect_pep668_break_flag()` method runs `ls
/usr/lib/python3*/EXTERNALLY-MANAGED` on the target once, caches
the result. All 4 `pip3 install` callsites thread the flag (empty
string on pre-PEP 668 systems whose pip doesn't recognize the
option). Plus a belt-and-suspenders retry: if detection misses but
pip's stderr says `externally-managed`, retry with the flag.

Pinned by `tests/test_v047_installer_pep668.py` (6 tests).

### Fix 6 — Device template audit (4 silent bugs)

Audit of `utils/device_templates.py` against the actual
`AddDeviceDialog` widget names surfaced four templates that
referenced non-existent widgets. `apply_to_dialog` uses
`getattr(dialog, name, None)` so the assignments were silently
dropped — operator picked a template, got dialog defaults instead
of the promised config.

| Template | Pre-fix field | Real widget | Impact |
|---|---|---|---|
| `ibgp_peer` | `bgp_protocol_type` | `protocol_dropdown` | iBGP claim dropped |
| `ebgp_peer` | `bgp_protocol_type` | `protocol_dropdown` | eBGP claim dropped |
| `isis_l12` | `isis_area_input` | `isis_area_id_input` | Lucky non-bug (defaults matched) |
| `isis_l12` | `isis_level_combo` | (no such widget) | Level claim dropped |
| `vxlan_vtep` | (4 widgets unset) | All exist on dialog | Summary lied — VNI=5000 not 10000, Bridge SVI empty |

`protocol_dropdown` items are populated *dynamically* when BGP is
enabled, so a `fields` assignment can't drive it. Added a
`_select_bgp_protocol(dialog, "iBGP"|"eBGP")` helper called from
`post_apply` that force-runs the cascade then selects the right
item AND ticks `bgp_use_loopback_checkbox`.

Plus a catalog-wide invariant test
(`test_every_template_field_references_a_real_widget`) that walks
every field of every template against the dialog source — future
drift surfaces here, not at the operator's chair.

### Feature 1 — Nine new scale-stream templates

Operator asked for templates covering source MAC, source/destination
UDP and TCP ports. The pre-v0.4.7 catalog had MAC-dst / IPv4 src+dst
/ IPv6 dst / 5-tuple (UDP) / VLAN-ID (six entries). New:

- `mac_src_sweep_1k` — source-MAC sweep × 1024
- `mac_src_and_dst_sweep_1k` — both-ends MAC learning
- `udp_src_port_sweep_1k`, `udp_dst_port_sweep_1k`
- `tcp_baseline` — the missing "I want TCP" starter
- `tcp_src_port_sweep_1k`, `tcp_dst_port_sweep_1k`
- `tcp_5tuple_sweep_rss` — TCP RSS bucket spread
- `ipv6_src_sweep_64` — mirror of v0.3.11 IPv6 dst sweep

Pinned by `tests/test_v047_scale_templates.py` (13 tests).

### Feature 2 — Searchable template dropdown

The catalog grew 14 → 23 entries; scrolling for the right scenario
got tedious. Made the AddStreamDialog template combo editable with
a case-insensitive `Qt.MatchContains` `QCompleter`. Typing "tcp"
narrows to 4 templates, "src" to 6, "rss" to 2, "1024" to 5, etc.
The dropdown still works as a regular combo (click the arrow).

### Feature 3 — Four new device templates (gap-fill)

- `rocev2_target` — RDMA target (DSCP=46 lossless, Priority=3,
  UDP/4791)
- `dhcp_server` — DHCP server (pool 192.168.30.10-200, 1h lease)
- `bgp_ospf_pe` — PE router (eBGP external + OSPFv2 area-0)
- `ipv6_only_host` — IPv6-only (v4 OFF, v6 ON)

Pinned by `tests/test_v047_device_templates_audit.py` (11 tests).

---

### Operator action

Upgrade target hosts to v0.4.7. The phantom `enp181s0f0np0.1`
sub-iface from before v0.4.5 (if still present) persists at the
kernel level — either run `sudo ip link delete enp181s0f0np0.1`
once, or it'll get removed the next time a Tagged stream finishes.

Fresh installs on Ubuntu 24.04+ now work out of the box (Fix 5).

## [0.4.5] - 2026-06-06

**No more phantom `<rx_iface>.1` sub-interface on Untagged streams.**

Operator-reported bug from svl-d-ai-srv04: started a Scapy stream
with flow tracking on RX iface `enp181s0f0np0`. The Interface
Statistics table started showing THREE interfaces — the expected
`enp160s0f0np0` + `enp181s0f0np0`, plus a phantom
`enp181s0f0np0.1`. The stream was configured `VLAN: Untagged`.

Root cause: `_build_rx_selector_for_stream` always pulled
`protocol_data.vlan.vlan_id` into the selector regardless of the
top-level `VLAN` field. The GUI defaults `vlan_id` to `"1"` even
for "VLAN: Untagged" streams, so the selector ended up with
`vlan_id=1`. `start_rx_counter` then saw a non-None vlan_id and
unconditionally called `_ensure_vlan_rx_visible(rx_iface, 1)`,
which created the phantom `enp181s0f0np0.1` sub-interface and
attempted to sniff on it. The stream's actual untagged frames
arrived at the base interface, so the v0.4.1 rescue sniffer
caught them (rx_count was correct), but the phantom interface
cluttered the Stats table.

### Fix

Selector now reads the top-level `VLAN` field (from `stream_data` OR
`protocol_selection`, case-insensitive) and only includes `vlan_id`
when it's `Tagged` / `Stacked` / `TaggedStacked`. For Untagged
streams (or missing field), `vlan_id=None` → no sub-iface gets
created.

### Tests

`tests/test_v045_vlan_subif_only_when_tagged.py` — 5 tests
pinning:

- Untagged stream produces `vlan_id=None` in the selector (no
  sub-iface)
- Tagged stream still gets `vlan_id` honored (legitimate case)
- Missing VLAN field defaults to untagged behaviour
- VLAN field value is case-insensitive (Tagged / tagged / TAGGED)
- VLAN field inside `protocol_selection` (not just top level) is
  also honored

Full suite: 1381 passed, 1 skipped.

### Related: Problem 2 — `enp181` shows 0 RX while TX is flowing

In the same screenshot, `enp181s0f0np0` showed 0 Received Frames
during an active 986 fps send. This is most likely either:

- **Polling-interval timing.** The Interface Stats dialog polls
  `/api/interfaces` every ~5 sec; the screenshot may have caught the
  table between polls or right at stream start. The v0.4.4
  `psutil.net_io_counters` snapshot is realtime — the kernel
  counter IS updating in `/proc/net/dev` continuously; the dialog
  just hasn't pulled the latest yet.

- **Mellanox HW VLAN offload stripping the tag.** If the v0.4.3
  TX-VLAN diagnostic (`_diagnose_tx_vlan`) logged
  `tx-vlan-offload=on` at stream start, the Dot1Q never made it
  onto the wire — but with v0.4.5 the stream is now correctly
  untagged on selector too, so the rescue sniffer on base catches
  everything. The stream-level rx_count should be matching now.

Neither needs a code fix in v0.4.5 — they're operator-side
observability + hardware-configuration issues. The v0.4.3
`/api/streams/<id>/rx_debug` endpoint surfaces sniffer state
including counters; check that to confirm packets are being
counted, then check `journalctl -u netgen-server | grep TX-VLAN`
for the offload diagnostic.

## [0.4.4] - 2026-06-06

**Two operator-reported bugs from `svl-d-ai-srv04`** — both root-caused
and fixed.

### Problem 1: TX != RX on stream stop (2.47% "loss" on a lossless link)

Operator stopped a stream and saw TX=13,312 / RX=12,983 — 329-packet
shortfall, ~2.47% "loss" on what should be a lossless lab loopback.

Root cause: `stop_event` fires for BOTH the TX thread and the RX
sniffer at the same moment. TX halts immediately, but its last-batch
in-flight packets (already in the NIC TX ring + on the wire + in the
RX-side kernel queue) take a few hundred milliseconds to work
through to libpcap. The sniffer stops first → those in-flight
packets never count.

Fix: 2-second drain inside the sniffer's stopper closure. After
`stop_event.wait()` returns, the background thread sleeps 2s
BEFORE calling `sniffer.stop()` — gives libpcap time to deliver
the last in-flight packets to the matching handler. 2 sec at typical
500 pps Scapy speeds = 1000 packets of drain capacity, well above
the observed 329-packet shortfall. Operator stop-click latency is
unaffected (background thread runs after the main stopper returns).

### Problem 2: TX iface shows RX packets, RX iface shows TX packets

Operator's interface-stats table showed `enp160` (the TX side) with
RX=12,544 packets and `enp181` (the RX side) with TX=12,566 packets.
Backwards from physics — `enp160` is sending, `enp181` is receiving.

Root cause: `/api/interfaces` was returning **literal random numbers**:

```python
tx = random.randint(100, 1000) if is_up else 0  # Transmitted packets
rx = random.randint(50, 800) if is_up else 0   # Received packets
sent_bytes = tx * random.randint(64, 1500)  # Simulate bytes sent
received_bytes = rx * random.randint(64, 1500)  # Simulate bytes received
errors = random.randint(0, 10) if is_up else 0  # Simulate errors
```

The comments literally said "Simulate". The random values happened
to land near the stream's real TX count by coincidence, making the
table look real but with TX/RX flipped.

Fix: read REAL counters via `psutil.net_io_counters(pernic=True)` —
`packets_sent` / `packets_recv` / `bytes_sent` / `bytes_recv` / 
`errin + errout`. One snapshot per request, mapped through to the
existing API shape. Now `enp160` shows actual TX, `enp181` shows
actual RX, and they correlate with the stream-level counts the
flow tracker maintains.

### Tests

`tests/test_v044_stats_fixes.py` — 4 new tests:

- `/api/interfaces` source has NO `random.randint` calls for tx/rx/
  bytes/errors (regression guard)
- Source DOES reference `psutil.net_io_counters(pernic=True)` +
  the four real field names
- `start_rx_counter`'s stopper has a `time.sleep` between
  `stop_event.wait()` and `sniffer.stop()`
- Sleep value is >= 1 second (covers the typical in-flight window)
- Sleep is positioned inside the stopper closure (background thread),
  not inline (which would block the main caller)

Full suite: 1376 passed, 1 skipped.

## [0.4.3] - 2026-06-06

**Audit batch: latent bugs + observability gap + TX-VLAN diagnostic.**
Six small fixes surfaced by the audit after v0.4.2 shipped — each
either silently broken before, or actively making future diagnostics
harder.

### Bugs squashed

1. **`/api/traffic/rx_monitor` removed** — endpoint at line 773
   called `start_rx_counter(interface, stream_name, stop_event,
   match_criteria)` with 4 args, but the function takes 6 required.
   TypeError if anyone hit it. No client in the repo did, but it was
   a latent footgun. The endpoint served no real purpose (the RX
   sniffer is correctly orchestrated by `generate_packets` via
   `/api/traffic/start`), so it's deleted rather than fixed.

2. **`/api/rfc2544/status` as alias for `/progress`** — operator hit
   `/status` while diagnosing the test progression and got 404; the
   real endpoint was `/progress`. Both routes now point at the same
   view function. Doc snippets / curl examples that reference
   `/status` no longer break.

3. **Runtime state files gitignored** — `recent_sessions.json` added
   to `.gitignore`; `session.json` + `server_interfaces.txt` already
   listed but were TRACKED (added before .gitignore caught them) so
   git rm --cached'd to actually drop them. No more `M session.json`
   noise in every git status.

4. **`pyproject.toml` excludes `build*` from packages.find** — local
   `python -m build` invocations on the same checkout were nesting
   `build/lib/build/lib/build/lib/...` inside the wheel
   (8 levels deep on the artifact I inspected earlier in the session).
   CI runners start fresh so don't see this; but local dev builds
   would. Explicit `build*` exclude makes the wheel clean
   regardless.

### Observability gap closed

5. **New `/api/streams/<stream_id>/rx_debug` endpoint** — exposes the
   RX sniffer's internal state for a flow-tracking stream:
   - `sniff_iface` + `base_iface` (primary listener + base, for
     the v0.4.1 dual-sniff)
   - `vlan_subif_created` + `vlan_subif_refcount`
   - `bpf` filter actually applied
   - `signature_pattern` regex
   - `seen_total` / `matched` / `sig_hits` / `tuple_hits` counters
     mirrored from the sniffer's local state every time lfilter
     fires
   - `relaxed_now` (auto-relax kicked in)
   - `rescue_active` (v0.4.1 fallback sniffer started)

   Diagnosing the v0.4.1 flow-tracking bug on `svl-d-ai-srv04` took
   ~15 min of SSH spelunking + tcpdump + journalctl + ip-link
   gymnastics; this endpoint would have made it a single `curl`.

### Diagnostic

6. **TX-VLAN startup diagnostic** — `_diagnose_tx_vlan(interface,
   sample_pkt, vlan_id_expected)` runs once per stream at "TX loop
   enter". Two things logged:
   - Whether the built packet carries a Dot1Q layer when
     `vlan_id_expected > 0` (catches a hypothetical builder regression)
   - Whether `ethtool -k <iface> | grep tx-vlan-offload` shows `on`;
     if so, WARNS with the operator-actionable fix
     (`ethtool -K <iface> txvlan off`). Mellanox/Intel firmware
     with `tx-vlan-offload=on` strips Dot1Q from Scapy frames and
     re-inserts from `skb->vlan_tci` (which Scapy's sendp path
     doesn't populate). This is almost certainly the root cause
     of the operator-reported "VLAN tagged config → untagged
     wire" bug from v0.4.1 — but rather than guess, the diagnostic
     surfaces the state so operators know whether to disable the
     offload or look elsewhere.

   Logs at most 2 lines per stream startup. Zero hot-path cost
   (single `ethtool -k` subprocess + a layer-presence check).

### Tests

`tests/test_v043_cleanups.py` — 10 new tests pinning each fix
above. Most are static-analysis style (regex over source) for
the endpoint removals + gitignore + pyproject excludes; the
TX-VLAN diagnostic also has 3 behavior tests covering the
no-Dot1Q warning, tx-vlan-offload-on warning, and the quiet
case (no VLAN expected).

Full suite: 1372 passed, 1 skipped.

## [0.4.2] - 2026-06-06

**Stream statistics table shows the real name, not "Unnamed Stream".**
Operator-reported bug from svl-d-ai-srv04: dialog had stream name
'ICMP' set correctly, but Stream statistics table showed
"Unnamed Stream" for the running row.

### Root cause

The client sent the stream payload with name 'ICMP' inside
`protocol_selection` but NOT at the top level. Both
`run_tgen_server.launch_single_stream` (line 877) and the streams
loop in `restart_stream` (line 989) used the dumb fallback
`stream_data.get("name", "Unnamed Stream")` and stored the row
as "Unnamed Stream". The existing `_resolve_stream_name` helper
in `multithreaded_traffic_gen.py` already walked the proper
fallback chain (top-level name / stream_name / display_name /
title → protocol_selection.name / stream_name → composite
`<port> / <L4> [<short-id>]`) — but the server-side launch paths
weren't using it.

### Fix

Both spots in `run_tgen_server.py` now delegate to
`_resolve_stream_name`. The user's 'ICMP' stream now renders as
'ICMP' in the stats table. Future clients that omit the top-level
name (e.g. on restart from a saved snapshot where the name is
nested) get a sensible composite, never "Unnamed Stream".

### Tests

`tests/test_stream_name_resolution.py` — 8 new tests pinning:

- Top-level 'name' takes precedence over protocol_selection
- Falls back to protocol_selection.name when top-level missing
- 'stream_name' alias supported at top level
- Placeholder 'Unnamed Stream' value treated as unset
- Blank / whitespace-only names treated as unset
- Composite fallback uses port / L4 / shortid
- Composite uses interface when port not set
- `run_tgen_server.py` has NO raw
  `get("name", "Unnamed Stream")` assignments left + DOES import
  and call `_resolve_stream_name`

Full suite: 1362 passed, 1 skipped.

## [0.4.1] - 2026-06-06

**Flow tracking on VLAN-tagged streams no longer stays at rx_count=0.**
Operator scenario from svl-d-ai-srv04: configured a Scapy stream with
`VLAN: Tagged + vlan_id=10`, started TX on enp160 with flow tracking,
RX interface enp181. tx_count incremented past 460k while
rx_count stayed at 0 indefinitely.

### Root causes (two compounding bugs)

1. **TX side**: stream config said tagged, but `tcpdump -i enp181 -e`
   showed UNTAGGED frames on the wire. Either Mellanox hardware VLAN
   offload was stripping the tag before transmission, or the Scapy
   builder path elided it for this specific stream config. Either
   way, the wire format didn't match what the RX sniffer expected.
2. **RX side**: the sniffer was bound to the temporary VLAN
   sub-interface `enp181s0f0np0.10` created by `_ensure_vlan_rx_visible`.
   Two issues:
   - The sub-iface was **deleted while the sniffer was still using
     it** (a previous stream's stop path raced ahead and ran
     `ip link delete`). Sniffer became a zombie bound to a
     non-existent device.
   - Even if it stayed alive: the sub-iface only sees VLAN-10
     frames, and there were none (per bug #1), so it had nothing
     to count.

### Fix — three pieces, all in `multithreaded_traffic_gen.py`

* **Sub-interface ref-counting** (`_VLAN_SUBIF_REFS` + lock,
  `_ensure_vlan_rx_visible` increments, new `_release_vlan_subif`
  decrements). The actual `ip link delete` only runs when the
  refcount reaches 0. Two streams sharing a VLAN can't blow each
  other's sub-iface away anymore.
* **Dual-sniff with rescue path**. When a VLAN sub-iface is
  created, the RX path now ALSO starts a sniffer on the BASE
  interface. So untagged frames (the bug #1 case) are still
  counted. Both sniffers feed the same handler; per-`<seq>` dedup
  prevents double-counting when both see the same packet.
* **Per-seq dedup** in the sniffer's `on_pkt`. The Scapy TX path
  embeds `[<stream_id>#<seq>]` in payload; we extract the seq and
  only increment rx_count once per (stream, seq) tuple. Bounded
  to ~50k seqs in memory (~5 MB max), trimmed by halves on
  overflow.

### Operator impact

- Existing streams that were stuck at `rx_count=0` because of the
  VLAN-tag mismatch now count correctly via the rescue sniffer on
  the base interface.
- Concurrent streams sharing the same VLAN no longer trample each
  other's sub-iface lifecycle.
- No behaviour change for streams without VLAN — the rescue sniffer
  only starts when a sub-iface was actually created.

### Known issue (not fixed in this release)

- The TX side bug (Dot1Q layer not making it onto the wire despite
  config saying tagged) is **NOT yet root-caused**. The fix above
  works around it by always sniffing the base interface in parallel.
  If you need the on-wire frame to actually carry the VLAN tag,
  check the Mellanox hardware VLAN offload setting:
  `ethtool -k <tx_iface> | grep tx-vlan-offload` and disable if
  needed: `ethtool -K <tx_iface> txvlan off`.

### Tests

`tests/test_vlan_subif_refcount.py` — 6 new tests pinning:
- Each `_ensure_vlan_rx_visible` call bumps the refcount
- First release while refcount > 0 does NOT delete
- Final release at refcount = 0 DOES delete and removes the entry
- Releasing an unknown sub-iface is a safe no-op
- Releasing empty string / None doesn't crash
- End-to-end "two streams share VLAN" scenario: A stops first,
  B keeps using the sub-iface; only B's stop actually deletes

Full suite: 1354 passed, 1 skipped.

## [0.4.0] - 2026-06-06

**Major release: RDMA Topology Test (N×M), RFC 2544 hardening, and a
fully searchable Help system.** Eleven commits since v0.3.18 split across
four operator-facing themes — each driven by real lab work on
`svl-d-ai-srv04` (Ubuntu 24.04 + ConnectX-7).

### RDMA Topology Test — N×M perftest orchestrator

The single-pair Blast a RDMA Flow dialog forced operators to open N
separate dialogs to drive N test pairs. Topology Test (Tools → RDMA →
Topology Test…) collapses that into one dialog with endpoint groups +
a topology shape, mirroring Ixia's IxNetwork Topology + Traffic Item
model.

- **New module** `utils/rdma_topology.py` (pure functions, no Qt) with
  `RdmaTopologyEndpoint`, `RdmaTopologySpec`, `expand_pairs()`,
  `validate_spec()`, `aggregate_stats()`. Bit-identical math to the
  dialog's TOTAL row.
- **New dialog** `widgets/rdma_topology_dialog.py` — compact layout, five
  topology shapes (single / fan-in / fan-out / mesh / pairwise),
  endpoint editors as plain-text panes (one endpoint per line:
  `<tg_url> <device> [port=N] [gid=N] [label=NAME]`), live "X pairs"
  preview label that surfaces validation errors as the operator types,
  per-pair stats grid + TOTAL row aggregating BW + MsgRate across
  pairs (iteration-weighted mean for latency tests).
- **Listen-port allocation** is `base + pair_index` across the whole
  topology — handles the FAN_IN case where one server endpoint
  participates in multiple pairs (each needs its own listening
  perftest process).
- **45 tests** in `test_rdma_topology_spec.py` (27) and
  `test_rdma_topology_dialog.py` (18) pinning shape correctness,
  unique-port invariants, aggregation math, parser edge cases.

### RDMA Blast dialog polish

Six operator-reported issues from real-world testing on srv04:

1. **perftest-retry poll** — when the initial probe sees
   `installed: false` (operator opens dialog mid-v0.3.18-auto-install),
   re-probe every 5 sec for up to 2 min so the red banner clears
   automatically once the binary lands.
2. **None wall-of-text** — perftest is batch-mode; final_* fields stay
   None until test completes. Pre-fix the chunk renderer showed
   `size=NoneB iters=None BW avg=None Gbps...` on every poll. Now
   shows `(perftest emits results on completion, not during run —
   12s elapsed)`.
3. **Per-side finished tracking** — `_is_finished(side, job, want_side)`
   returned False whenever side != want_side, so `_on_both_finished()`
   was never called, the poll timer never stopped, and "done" lines
   were re-appended every 2 sec forever. Fixed via instance-level
   `_server_finished` + `_client_finished` flags.
4. **Render dedup** — `_last_rendered_key` per side; only re-renders
   on state transitions.
5. **Compact + professional layout** — 4-sentence header → 1 line,
   8-row Test params QFormLayout → 2-column 5-row grid, slate-200
   GroupBox borders, fixed-width spinboxes.
6. **Port spinbox tooltip** — "almost always 1 on modern Mellanox;
   each port is its own IB device."

### RFC 2544 — smart search + reachability + live progress + Scapy guard

Operator-reported scenario (svl-d-ai-srv04, 400 G Mellanox NIC):
started RFC 2544 with DPDK unchecked, frame_size=64. Got "Testing
64B" then a wall of dashes 60 seconds later. Investigation: Scapy's
TX ceiling is ~500 kpps; RFC 2544 at 64 B probes at line rate
(~595 Mpps on 400 G). Scapy actually sent 80k packets in 60 sec
(~1.3 kpps), RX=0 (peer unreachable), 100% loss → naive search:
13 iterations × 60 sec wasted before converging to 0 pps.

Four fixes:

- **Pre-flight Scapy warning** — fires when DPDK is unchecked AND the
  test includes frame sizes ≤256 B. Explains Scapy ceiling vs line
  rate at this size, offers three sensible paths: enable DPDK / run
  on a slow link / continue anyway. Operator override via Yes; default
  No aborts without POST.
- **Smart binary search** (new `utils/rfc2544._decide_step()`) detects
  `tx_pps_actual < 10% of trying_pps` and uses the actually-achieved
  rate as the new ceiling. Operator's case: converges in 1 iteration
  (was 13) with diagnosis="tx_rate_limited" surfaced in the progress
  payload so they know WHY it's 0.
- **Reachability pre-flight** — best-effort ICMP ping via the test
  interface (`ping -c 1 -W 2 -I <tx_iface> <ip_dst>`) before kicking
  off the test thread. Returns HTTP 409 with
  `{warning: "destination_unreachable"}` so the client can show a
  confirm dialog. Auto-skipped on loopback (rx_iface == tx_iface or
  unset). Overrides: `confirm_unreachable=true` (one-shot) or
  `skip_reachability_probe=true` (always skip).
- **Live in-flight row updates** — server now mirrors the running
  attempts list + iteration counter into `_RFC2544_STATE["current_step"]`
  after each iteration. Client renders the in-flight row as
  `trying 74,404,761 / loss 100.0% / iter 3` instead of leaving it as
  dashes for 13+ minutes. Resets to `—` when the search moves on.

### Compact dialog layouts

- **RFC 2544 dialog**: 9-row vertical QFormLayout → 2-column QGridLayout
  (~150 px reclaimed), action+status+close inline on one row (was 2
  rows), slate stylesheet matching RDMA Blast, results table now read-
  only with hidden row headers. Geometry 820×640 → 900×560.

### Help system overhaul

- **New Help → RDMA Guide** menu entry with a 21 KB standalone guide
  (architecture, install/auto-install, device model, both dialogs,
  topology shapes, stats aggregation, operator workflows, Ixia/Spirent
  comparison, troubleshooting, REST surface). Previously the Topology
  + Ixia content was buried inside the API Guide as §10d/§10e where
  operators couldn't find it — operator reported "I don't see the
  Ixia comparison" + "I'm looking for a separate menu item for RDMA
  in the help".
- **TOC sidebar + search box on every help dialog** (Install, API,
  RDMA, What's New, Supported Features, DPDK). `_open_help_dialog`
  refactored to split into left-sidebar TOC populated from h2/h3
  headers + top-bar search with Find Next/Prev, Enter/Shift+Enter
  shortcuts, live match-count, wrap-around.
- **TOC scroll-to-top fix** — operator reported clicking a TOC item
  landed the heading at the BOTTOM of the viewport (Qt's
  `ensureCursorVisible()` does the minimum scroll). Replaced with
  direct `verticalScrollBar.setValue()` based on the matched block's
  absolute document Y coordinate.
- **API guide §28i** — "Topology Mode (v0.4.0) — driving N×M perftest
  pairs over REST" with worked bash example for fan-in stress, listen-
  port allocation rules, aggregation math table. §28 intro
  cross-reference updated from stale "Install Guide §10" → "RDMA Guide".

### Upgrade notes

- Existing servers on v0.3.18 upgrading to v0.4.0 get all server-side
  fixes automatically (smart search, reachability pre-flight, RDMA
  topology endpoints — all live in the wheel).
- Client running on v0.3.18 against a v0.4.0 server: backward-compatible.
  Old client misses the live in-flight updates + Topology dialog +
  RDMA Guide; new server still serves it.
- New v0.4.0 client against a v0.3.18 server: Topology dialog works
  (it's purely client-side over existing /api/rdma/perftest/* endpoints).
  Live RFC 2544 in-flight updates degrade gracefully (current_step
  fields missing → row stays as "—" same as before).

## [0.3.18] - 2026-06-05

**Server-side auto-install of RDMA userspace closes the wheel-upgrade
gap.** Operator on `svl-d-ai-srv04` upgraded from v0.3.15 → v0.3.17 via
the Upgrade tab (wheel-only swap), then opened Tools → RDMA Blast and
hit the red "perftest is NOT installed on server TG. Install with
`apt install perftest`" banner. The wheel-only upgrade path doesn't
re-run `install_ostg_complete.py`'s system-deps steps, so a server
originally provisioned before v0.3.12 (when `_install_rdma_userspace`
was added) ends up with the new RDMA Python runtime but no perftest
binary. Per standing rule "user will not do such manual recovery",
the fix had to be server-side and automatic — no operator SSH step.

### What changed

- **New `utils/system_deps.py`** with
  `ensure_rdma_userspace_installed()` — daemon-thread-safe self-heal
  that detects missing perftest and runs the distro-appropriate
  install. Two-pass design from v0.3.17's `_install_rdma_userspace`
  is preserved: CORE (`perftest rdma-core libibverbs-dev`) only.
  `libmlx5-dev` (MOFED-only) is intentionally skipped — too easy to
  break the install on non-Mellanox hosts; operators on MOFED hosts
  still run Fresh Install for that header.
- **Wired into `run_tgen_server.py` main()** via a daemon thread
  right after the existing FRR REBUILD daemon-thread block. Mirrors
  the v0.2.18 startup-hang lesson exactly — never block Flask
  binding its port.
- **`tests/test_system_deps_auto_install.py`** — 19 tests pinning
  9 design properties (async/daemon-thread, once-per-uptime guard,
  60s timeout, distro-aware, idempotent, dedicated log file,
  kill-switch env var, never raises, non-root skip) + a wire-in
  verification that `main()` actually spawns the thread.

### Design properties (each pinned by tests)

1. **Async off the Flask startup critical path** — daemon thread,
   doesn't block port bind.
2. **Once-per-uptime guard** via module-level `_attempted` flag +
   threading.Lock — concurrent callers race on the lock but only
   one invokes apt.
3. **Time-bounded** — 60 s ceiling on every subprocess. Stuck
   mirrors can't hang the thread forever.
4. **Distro-aware** — apt / dnf / yum / apk / zypper. Mirrors
   `install_ostg_complete.py._install_rdma_userspace` package
   lists exactly.
5. **Idempotent** — `shutil.which("ib_send_bw")` skip on
   pre-installed hosts (zero apt cost on startup for the
   common case).
6. **Dedicated log** — `/var/log/netgen-auto-install.log`,
   timestamped lines, separate from Flask request logs.
7. **Kill-switch env var** — `NETGEN_AUTO_INSTALL=0` lets managed
   systems opt out without changing the wheel. Accepts the common
   off-spellings (`0`, `false`, `False`, `no`, `off`, `OFF`).
8. **Never raises** — public API contract; daemon thread can't
   crash the server on a malformed apt response.
9. **Needs root** — server runs as root by default (VRF/DPDK
   require it). Non-root invocation logs a WARNING and skips
   rather than crashing on permission denied.

### Operator impact

- Upgrading any pre-existing server (any version) to v0.3.18 via the
  Upgrade tab now automatically installs perftest + rdma-core +
  libibverbs-dev on next service restart. No SSH step.
- `svl-d-ai-srv04` specifically: upgrade from v0.3.17 → v0.3.18, the
  daemon thread fires within the first ~2 sec of server start,
  perftest lands within ~30 sec, Tools → RDMA Blast works
  immediately without operator intervention.
- Managed systems where apt changes by background processes are
  disallowed: add `Environment=NETGEN_AUTO_INSTALL=0` to the
  systemd unit override.
- Audit trail: `tail -f /var/log/netgen-auto-install.log` shows
  what was installed when.

## [0.3.17] - 2026-06-05

**Install hardening, RDMA netdev names, and a quiet pip trap.** Fresh
install on a bare Ubuntu 24.04 box (`svl-d-ai-srv04`) surfaced 9
distinct failure modes in the install path — every one operator-
reported during a real upgrade, every one fixed without requiring the
operator to SSH in for manual recovery. Plus a usability fix for the
RDMA Blast device picker and one quiet pip trap that bites every
same-version wheel rebuild.

### Install path — fresh-install bugs squashed (Ubuntu 24.04)

1. **SFTP recursive upload fails on parent-dir missing** — paramiko's
   `sftp.put` has no `mkdir -p` semantics. Walking each path
   component now in `_sftp_makedirs` before uploading the
   `resources/dpdk/tx_worker/build/` tree.
2. **Install dialog popup goes silent during legitimate quiet
   stretches** — adaptive backoff was fine, but no visible
   heartbeat. 30 s heartbeat line + `[INFO]`/`[WARNING]`/`[ERROR]`
   step parser now updates the status label every chunk.
3. **pip bootstrap fails on Ubuntu 24.04** with
   "uninstall-no-record-file × Cannot uninstall packaging 24.0".
   Replaced the curl get-pip.py pipe with an `ensurepip` →
   distro `python3-pip` → `get-pip.py --ignore-installed`
   fallback chain.
4. **apt-get install fails on dpkg conffile prompt** — `apt-get
   install -y` only auto-answers apt's prompts, not dpkg's
   conffile diff prompts. New `_apt_install` wrapper appends
   `-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold`
   to every install. `install_dpdk.sh` gets the same treatment
   inline. The v0.3.16 docker-ce failure site (containerd.io's
   config.toml diff) now installs cleanly.
5. **gpg --dearmor fails in detached install** with `cannot open
   '/dev/tty'`. Modern GnuPG 2.x defaults to interactive; new
   `_install_apt_keyring(name, key_url)` helper downloads + dearmors
   with `--batch --no-tty --yes` and chmods the keyring to 0644.
   The Docker keyring install uses this helper now.
6. **Installer self-heals from half-configured dpkg state on retry**
   — operator hit "Dependency failed for netgen-server.service"
   from a half-installed Docker stack left by a prior failed run.
   New `_heal_dpkg_state()` pre-flight at the top of `install_local`/
   `install_remote`: `dpkg --audit` → force-remove half-installed
   Docker stack → `dpkg --configure -a --force-confdef --force-confold`
   → `apt-get clean / update`. No more SSH-in manual recovery.
7. **Install log popout shows blank** — invisible-text bug from a
   hardcoded dark `#0f172a` background vs the default light text
   color. Popout now inherits the inline view's stylesheet.
8. **Install log popout — text overlap from shared-document font
   mismatch** — QPlainTextEdit's shared QTextDocument computes
   per-block line heights against the FIRST view's font. The
   popout was appending its own `font-size: 12px` to the inline
   view's 11px on the shared document. Dropped the font-size
   override and added explicit `view.setFont(self.log_view.font())`.
9. **libmlx5-dev all-or-nothing fails RDMA install on non-MOFED
   hosts** — `_install_rdma_userspace` lumped 4 packages into one
   `apt-get install`. apt is all-or-nothing per command, so on a
   MOFED-less host, the entire batch failed even though 3 of 4
   packages were in Ubuntu main. Now split into two passes:
   CORE (`perftest rdma-core libibverbs-dev`, must succeed) +
   MOFED-OPTIONAL (`libmlx5-dev`, warns on failure). Same split
   in `install_dpdk.sh`'s build-deps command.

### Install path — distribution fixes

10. **Bundle install_ostg_complete.py into .dmg/.exe via PyInstaller
    spec** — without this, the in-GUI Fresh Install dialog couldn't
    find the installer on a fresh `.dmg`/`.exe` install. The spec
    `datas=[]` now includes the installer script.
11. **Fix B: bundle wheel in .dmg/.exe + `_guess_wheel_path()`
    auto-fill** — the dialog's wheel field now auto-fills from
    the wheel bundled inside the PyInstaller app bundle, with
    mtime-newest-wins glob over `dist/`. New users on a fresh
    `.dmg`/`.exe` install no longer need to know where the wheel
    lives — Fresh Install becomes truly one-click.
12. **pip3 install --force-reinstall --no-deps for same-version
    wheel rebuilds** — without `--force-reinstall`, rebuilding the
    wheel from current source (same version string, new contents —
    the "Option A" rebuild path) is silently skipped by pip with
    "Requirement already satisfied". Adds a separate deps-only
    pass for fresh-install dep resolution. `--no-deps` keeps the
    in-place artifact swap fast and avoids distutils conflicts
    on OS-managed packages.

### RDMA usability

13. **Surface kernel netdev names in RDMA device picker + Devices
    view.** Operators couldn't correlate abstract `mlx5_N` IDs
    with their `ip link` output (`enp175s0f0`, `eth4`, etc.).
    `utils/rdma_perf.py` gained `_list_net_ifaces(dev)` walking
    `/sys/class/infiniband/<dev>/device/net/`; `RdmaDevice`
    dataclass gained `net_ifaces: List[str]`; the combo label
    in `widgets/rdma_blast_flow_dialog.py` and the Devices viewer
    in `traffic_client/rdma_menu_actions.py` now render an
    `iface=enp175s0f0` tag after the device name.

### Tests + docs

- **13 new test files, +90 tests** pinning every install-path fix
  + the RDMA netdev surfacing. Test list:
  `test_install_sftp_makedirs.py`, `test_install_dialog_progress.py`,
  `test_pip_bootstrap.py`, `test_apt_noninteractive.py`,
  `test_pyinstaller_spec_bundles_installer.py`,
  `test_install_log_popout.py`, `test_dpkg_heal_preflight.py`,
  `test_apt_keyring_helper.py`, `test_rdma_install_split.py`,
  `test_pip_force_reinstall.py`.
- **Install guide refresh** (in-app `_INSTALL_GUIDE_HTML` +
  `INSTALL.md`): new §3a "What files do I actually need?", §3c
  wheel-only disclaimer, rewritten §4 "What gets installed"
  reflecting actual 0.3.16+ install methods (`_heal_dpkg_state`,
  `_install_apt_keyring`, `_apt_install`, two-pass RDMA install,
  `pip3 install --force-reinstall --no-deps`), §4a helper notes
  cross-referencing the regression tests. `INSTALL.md`'s
  "Server install" snippet no longer tells operators to clone
  the whole repo just for one file.

### Upgrade notes

Same-version wheel installs (e.g. swapping a hot-fixed
`0.3.17` wheel onto a 0.3.17 server) now actually take effect
because of the pip `--force-reinstall` fix. Pre-0.3.17 you had
to manually `pip3 uninstall ostg-trafficgen` first.

Fresh installs on Ubuntu 24.04 should now complete without
operator intervention. If `libmlx5-dev` fails on a non-Mellanox
host, that's expected — log says `[WARNING] libmlx5-dev not
available — skipping (host lacks Mellanox MOFED apt repo)`
and the install continues.

## [0.3.16] - 2026-06-03

**Bug fix: duplicate TG-id when adding servers.** Operator reported
adding two servers to the TGEN list and getting two rows both
labelled "TG 1" (instead of "TG 1" + "TG 2"). User's
`session.json` at time of report:

```json
{"server_interfaces": [{"tg_id": 1, "address": "http://svl-d-ai-srv01:5050"}]}
```

### Root cause

Every "add server" callsite computed `tg_id = len(self.server_interfaces)`
— the next ARRAY INDEX, not the next UNIQUE id. Brittle the moment
the existing list isn't 0-indexed contiguous:

- Session JSON saved with a non-zero `tg_id` (the user's case —
  `tg_id=1` after a likely earlier remove), reloaded next launch.
  `len([{tg_id:1}]) = 1` → new server gets `tg_id=1` too → both
  render as "TG 1" in `server_section.py:1261`'s
  `f"TG {server['tg_id']}"`.
- Remove-then-readd: started with `[{tg_id:0}, {tg_id:1}]`, removed
  the first → `[{tg_id:1}]`. Next add hits the same collision.

### Fix

New `traffic_client/menu_actions._next_tg_id(server_interfaces)` helper:
- Empty list → 0 (fresh install start unchanged).
- Else `max(s["tg_id"] for s in servers) + 1`.
- Defensive: coerces string `tg_id` from JSON; ignores garbage values;
  treats missing key as 0.
- Pure function — does NOT renumber existing entries (would break
  the session-persistence contract since saved streams + UI state
  reference servers by tg_id).

Applied at all 4 callsites that previously used the bare `len()`:
- `menu_actions.py:125` — main path (add via dialog)
- `menu_actions.py:201` — legacy two-step QInputDialog fallback
- `menu_actions.py:344` — re-add of a previously-removed server
- `main.py:410` — auto-add from env-var/default URL on startup

### Tests
- New `tests/test_next_tg_id.py` — 10 regression tests including:
  - The exact user scenario (`[{tg_id:1}]` → next must be 2)
  - Post-remove gap scenario
  - Sparse / hand-edited tg_ids
  - String-coerced JSON values
  - Missing key / garbage values
  - Sequential-add uniqueness across 3 operations
  - Pin that existing entries are not mutated

### Files changed
- `traffic_client/menu_actions.py` (new `_next_tg_id` helper + 3
  callsite replacements)
- `traffic_client/main.py` (4th callsite — lazy-imports the helper)
- `tests/test_next_tg_id.py` (new, 10 tests)

---

## [0.3.15] - 2026-06-03

**RDMA HCA capability surfacing + raised QP ceiling.** Triggered by
the operator question "how many QPs can the current implementation
support?" — the answer needed both a docs/code change and a real
hardware-cap surface to be honest.

### What changed

**Server — `utils/rdma_perf.py`**
- New `_query_ibv_devinfo(device_name)` helper subprocess-calls
  `ibv_devinfo -v -d <name>` (rdma-core, already a netgen install
  prereq) and parses the verbose output for 7 capability fields:
  `max_qp`, `max_qp_wr`, `max_cq`, `max_cqe`, `max_mr`, `max_pd`,
  `max_sge`. Graceful all-None return when the binary is missing,
  the subprocess times out, or the device name is empty — never
  raises into the caller.
- New `_parse_ibv_devinfo(blob)` pure-string parser; rejects hex
  bitfields (`max_mr_size`, `page_size_cap`) and strips
  "decorated" values like `262144 (special)` cleanly.
- `RdmaDevice` dataclass gains 7 optional cap fields; populated by
  `list_rdma_devices()` calling `_query_ibv_devinfo` per device.
  ~30 ms per HCA — serial probe is fine for the 1–16 HCA-per-host
  range; parallelise via ThreadPoolExecutor if you ever see 64+.
- `/api/rdma/devices` response now includes the cap fields on
  every device entry. Field doc'd in API Guide §RDMA.

**Client — `traffic_client/rdma_menu_actions.py`**
- `RDMA Devices…` viewer renders an "HCA caps" line per device
  with the 6 visible cap counts, pretty-formatted
  (`max_qp=262,144  max_cq=16.8M`). Falls back to
  "(ibv_devinfo not available — install rdma-core)" when the
  server returned all-None.

**Client — GUI QP-count ceilings**
- `widgets/rdma_blast_flow_dialog.py` — Blast a RDMA Flow QP-count
  spinbox: `setRange(1, 1024)` → `setRange(1, 131072)`. Matches
  typical Mellanox ConnectX-7 `max_qp`. Tooltip updated with the
  practical envelope (1–16 BW, 32–128 saturation, 256+ stress).
- `widgets/stream_dialog.py` — per-stream RDMA params QP-count
  spinbox: same range bump + tooltip pointing at the new RDMA
  Devices view for the per-HCA cap.

**Help guides**
- What's New: "0.3.15 highlights — RDMA QP ceiling visibility"
  block at top.
- API Guide §RDMA: `/api/rdma/devices` doc updated with the new
  cap fields contract + graceful-None semantics.

### Tests
- 11 new tests in `tests/test_rdma_perf.py`:
  - Parser correctness on the canonical ibv_devinfo blob
  - Hex bitfield rejection (max_mr_size, page_size_cap)
  - Decorated-value stripping
  - Empty / None blob → all-None
  - Missing-field handling (only max_qp present → siblings stay None)
  - Binary-missing graceful degrade
  - Empty device name graceful degrade
  - Subprocess timeout doesn't propagate
  - Partial output parsed on rc != 0
  - End-to-end `list_rdma_devices` integration with mocked sysfs
    + mocked ibv_devinfo (verifies caps land on the dataclass)
  - Same integration with ibv_devinfo missing (verifies graceful
    None pattern at the dataclass layer)
- RDMA suite total: 70 → 85. Full repo: 1119 → 1130 tests passing.

### Files changed
- `utils/rdma_perf.py` (parser + helper + dataclass extension)
- `traffic_client/rdma_menu_actions.py` (RDMA Devices viewer)
- `widgets/rdma_blast_flow_dialog.py` (QP range + tooltip)
- `widgets/stream_dialog.py` (3 places: per-stream QP range + API
  guide §RDMA + What's New highlights block)
- `tests/test_rdma_perf.py` (11 new tests)

---

## [0.3.14] - 2026-06-03

**Help-guide refresh — the docs catch up to v0.3.13.** v0.3.13
shipped parser fixes without touching the in-app guides; users
running v0.3.13 saw correct MTU + perftest version in the actual
features but the Help → What's New / Capabilities / API Guide /
Install Guide entries were still describing v0.3.12 behaviour.

### What changed

- **What's New** gains a "0.3.13 highlights — RDMA parser fixes" block
  above the v0.3.12 section. Explains the MTU enum issue + the
  perftest version-probe fallback chain, with the lab-verified
  316.48 Gbps benchmark as a credibility marker.
- **Capabilities → RDMA workflow** section refreshed end-to-end:
  pre-flight, the two paths, lab-verified facts, and a polished
  "Common pitfalls" TABLE (was a 3-item bullet list) covering 8
  failure modes with the GUI signal that surfaces each one and
  the corresponding fix. Includes the cross-NIC "Failed to modify
  QP to RTR" case (lab-confirmed) so operators recognise the
  symptom when peer routing isn't set up.
- **API Guide §RDMA** gains contract clarifications under
  <code>/api/rdma/devices</code> (MTU field is always BYTES,
  normalised from any sysfs format) and
  <code>/api/rdma/perftest/installed</code> (5-stage version
  fallback chain documented; explains why <code>version</code>
  can be null even when <code>installed=true</code>).
- **Install Guide §10** gains a "status check after install" muted
  note: tells operators where to look in the GUI to confirm the
  v0.3.13 parser fixes landed, vs the misleading v0.3.12 display.

No functional changes. No code surface changes. Tests unchanged
(74/74 RDMA, 1119/1119 full suite still green).

### Files changed
- `widgets/stream_dialog.py` (3 Help-guide HTML constants:
  `_API_GUIDE_HTML`, `_FEATURE_GUIDE_HTML`, `_CAPABILITIES_GUIDE_HTML`)

---

## [0.3.13] - 2026-06-03

**RDMA parser fixes from live-hardware verification.** v0.3.12 shipped
without lab validation; bringing it up on svl-d-ai-srv01 (6 Mellanox
HCAs, mlx5 driver, perftest 24.04.0-0.41) surfaced two cosmetic data-
parsing gaps in `utils/rdma_perf.py`. Functional RDMA worked
end-to-end (verified 316.48 Gbps loopback on mlx5_5 + clean SIGTERM
on Stop), but the **RDMA Devices viewer** showed misleading
`MTU: 0` on every NIC and `perftest installed (?)` instead of a
real version.

### What changed

**`utils/rdma_perf._parse_active_mtu` — new helper**
- Mellanox + most modern kernels (5.x+) write
  `/sys/class/infiniband/<dev>/ports/<n>/active_mtu` as a single
  IB MTU enum digit (`1` → 256 B … `5` → 4096 B per IBA spec §3.5.3).
- v0.3.12's regex `\d{3,5}` required ≥3 digits, so single-digit
  enum values returned 0. Result: every device showed `mtu: 0`.
- The new helper accepts all three formats:
  bare enum `"3"`, colon `"3: 1024"`, raw bytes `"1024"`, and the
  perftest-style `"4096[B]"` suffix. 16 parameterized test cases
  pin every format observed in the wild.
- srv01's mlx5_5 port 1 now correctly reports `mtu: 1024`
  matching perftest's own `Mtu : 1024[B]` banner line.

**`utils/rdma_perf._probe_perftest_version` — multi-stage fallback**
- v0.3.12 relied on `<tool> --version`, which on srv01's perftest
  build (24.04.0-0.41, the version shipped by Mellanox MOFED + the
  newer Ubuntu 24.04 perftest package) returns no useful output.
- New fallback chain (cheapest probe first):
  1. `<tool> --version` (older perftest works here)
  2. `<tool> -V` (some forks)
  3. `dpkg -s perftest` (Debian/Ubuntu — what works on srv01)
  4. `rpm -q perftest` (RHEL/Fedora)
  5. `apk info -v perftest` (Alpine)
- Returns None only if every probe fails — the GUI then renders
  just "perftest installed" without a version qualifier rather
  than 500-ing.
- 5 new tests: extract-version-from-blob parameterized over real
  format strings, fall-through-to-dpkg pin, all-probes-fail
  graceful-None pin.

### Lab verification on svl-d-ai-srv01 (Mellanox, 6 NICs)
- ✓ `/api/health` reports v0.3.13 after wheel upgrade
- ✓ `/api/rdma/devices` now shows correct MTU per port
  (1024 B on the active mlx5_5 port, was 0 in v0.3.12)
- ✓ `/api/rdma/perftest/installed` now reports
  `version: "24.04.0-0.41"` via the dpkg fallback (was null in v0.3.12)
- ✓ Loopback Send BW test on mlx5_5 hit 316.48 Gbps avg
- ✓ Cross-NIC test mlx5_3↔mlx5_0 failed cleanly with operator-
  readable "Failed to modify QP to RTR" (subnets unrouted) —
  proves the failure-surfacing path works as designed
- ✓ Stop button SIGTERMs the perftest child cleanly (rc=-15
  captured, pairing record dropped) — regression for the
  v0.3.12 audit-pass fix
- ✓ Blast a RDMA Flow dialog populates the device combo with all
  6 NICs incl. per-port state badges (ACTIVE vs DOWN)

### Tests
- 16 new parameterized tests for `_parse_active_mtu` covering
  every sysfs format variant observed.
- 5 new tests for `_probe_perftest_version` covering blob parsing,
  dpkg fallback, all-probes-fail graceful return.
- Suite: 1119 passing (up from 1119 — RDMA suite grew from 49 → 70).

### Files changed
- `utils/rdma_perf.py` (parser fixes — no API surface change)
- `tests/test_rdma_perf.py` (21 new test cases)

---

## [0.3.12] - 2026-06-03

**RDMA traffic generation release.** Adds end-to-end RDMA support via
the standard `perftest` suite (`ib_send_bw`, `ib_write_bw`, `ib_read_bw`
plus `_lat` variants) — both as a standalone two-TG benchmark dialog
and as a third per-stream engine alongside Scapy / DPDK.

### What changed (by area)

**Server — RDMA orchestrator**
- New `utils/rdma_perf.py` — replaces the v0.2.x 44-line stub
  (hardcoded `mlx5_0`, hardcoded test type, missing imports). Real
  orchestrator: sysfs-based RDMA device discovery (`/sys/class/
  infiniband/<dev>/ports/<p>/{state, link_layer, rate, active_mtu,
  gids/*}`), perftest install probe via `shutil.which`, per-job
  registry with thread-safe `Popen` lifecycle, stdout parsing for
  both BW and latency data rows, port allocator from 18515 up, TTL
  GC of finished jobs.
- New `utils/rdma_handshake.py` — lightweight pairing-tag broker
  that lets two TGs correlate their halves of a single Blast a
  RDMA Flow via a shared `handshake_id`.
- New `utils/rdma_stream_engine.py` — shim that bridges per-stream
  `engine = rdma` into `start_perftest("client", ...)`. Registers
  in `StreamTracker` so Streams-tab stats show running TX bytes/sec
  (synthesised from perftest's data row, polled every 2 s).
- Eight new `/api/rdma/*` routes in `run_tgen_server.py`:
  `GET /api/rdma/devices`, `GET /api/rdma/perftest/installed`,
  `POST /api/rdma/perftest/start`, `POST /api/rdma/perftest/stop`,
  `GET /api/rdma/perftest/jobs`, `GET /api/rdma/perftest/job/<id>`,
  `GET /api/rdma/handshakes`, `GET|DELETE /api/rdma/handshakes/<id>`.
- `/api/traffic/start` short-circuits to `start_rdma_stream` when
  `stream_data.engine == "rdma"`, bypassing the DPDK/Scapy
  pipeline.

**Client — Blast a RDMA Flow dialog (new)**
- `widgets/rdma_blast_flow_dialog.py` — non-modal dialog that picks
  a server-TG + RDMA device, a client-TG + device, a test type
  (Send / Write / Read × BW / Latency), and a parameter set
  (msg size, QP count, duration, MTU, GID index, bidirectional).
  Brokers the peer handshake via /api/rdma/perftest/start on each
  side, polls /api/rdma/perftest/job/<id> every 2 s, renders live
  BW (Gbps) / Msg rate (Mpps) for BW tests or min/avg/p99 (µs) for
  latency tests. Same multi-instance shape as Blast a DPDK Flow:
  open multiple to fan out across NIC pairs. Loopback test when
  only one TG is selected.
- `traffic_client/rdma_menu_actions.py` — 3 menu handlers:
  `show_rdma_blast_flow_dialog`, `show_rdma_devices_dialog`
  (multi-server `/sys/class/infiniband` viewer + perftest install
  state), `show_rdma_jobs_dialog` (active + finished jobs per TG).
- New "RDMA" submenu under `Tools` in `traffic_client/main.py`,
  sibling of the existing DPDK submenu.

**Per-stream RDMA engine — Add Stream dialog refactor**
- `widgets/stream_dialog.py` Runtime Engine tab: legacy
  `Use DPDK (tx_worker)` checkbox is now a backward-compat bridge.
  New authoritative **Engine** combo: Scapy / DPDK (tx_worker) /
  RDMA (perftest). Picking RDMA reveals a params group (test
  type, device, peer address, msg size, QPs, duration, GID index,
  bidirectional) saved/loaded with the stream.
- Backward compat: pre-v0.3.12 streams (only `dpdk_enable: true`)
  load as `engine = dpdk`. Save writes both `engine` AND
  `dpdk_enable` so older servers keep working.
- Streams tab Engine column gains an RDMA branch — shows
  "RDMA Send" / "RDMA Write" / "RDMA ReadL" etc. in purple
  (distinct from DPDK blue), instead of falling through to the
  default "Scapy" label.

**RDMA stop-bug fix (caught during audit pass)**
- `rdma_stream_engine.start_rdma_stream` was registering streams
  in `StreamTracker` with `tracker.add_stream(interface, sid, name)`
  — wrong signature (tracker expects a dict). And the
  `threading.Event()` minted in `/api/traffic/start` was orphaned
  — never landed in the tracker, so `/api/traffic/stop` couldn't
  reach the poll thread and the perftest child kept running until
  its full `--duration` expired. Fixed by passing the event into
  `tracker.add_stream({"stop_event": ...})` matching the
  DPDK/Scapy path exactly. Two regression tests pin it.

**Install pipeline — perftest auto-installed**
- `install_ostg_complete.py` gains `_install_rdma_userspace()`
  called from `install_system_dependencies` (every install path,
  including `--no-dpdk`). Installs `perftest rdma-core
  libibverbs-dev libmlx5-dev` (apt) with analogous packages on
  dnf/yum/apk/zypper. Wrapped in try/except so a missing package
  on an exotic distro doesn't break the main install. Verifies
  via `which ib_send_bw` post-install and logs a clear warning
  if perftest didn't land.
- `resources/dpdk/install_dpdk.sh` apt prereqs list appends
  `perftest` alongside `libibverbs-dev libmlx5-dev rdma-core` —
  any host that runs DPDK install or the Tools → DPDK → Install
  DPDK admin action gets RDMA capability too. Duplication with
  the python installer is intentional belt-and-suspenders
  (apt-get install on already-installed packages is a fast no-op).

**Help guides**
- Install Guide §10 documents the auto-install + manual recovery
  one-liners per distro, plus a RoCEv2 vs InfiniBand link-layer
  table and 4 common install gotchas.
- API Guide §28 lists all 8 `/api/rdma/*` endpoints with full
  curl examples — including a bash polling script that
  orchestrates a two-sided test end-to-end and the field table
  for /api/rdma/perftest/start.
- What's New gets a "0.3.12 highlights — RDMA traffic generation"
  block at the top.
- Capabilities (Supported Features): Quick-launch workflows table
  gains a Blast a RDMA Flow row; L3/L4 backend matrix gains an
  RDMA column with proper per-protocol marks.

### Tests
- 49 new tests across `test_rdma_perf.py`,
  `test_rdma_handshake.py`, `test_rdma_stream_engine.py`,
  `test_rdma_blast_flow_dialog.py` — all mock the subprocess so
  the suite runs on macOS / Linux without RDMA hardware.
- Argv builder pinned against all 8 supported perftest variants;
  stdout parser pinned against real BW + LAT data rows.
- Round-trip pins for the Engine combo: legacy `dpdk_enable=True`
  loads as `engine=dpdk`; RDMA params survive save→load.
- Two explicit regression tests for the stop-bug
  (`stop_event` round-trips through tracker; `add_stream` is
  called with dict shape).

### Files changed
- `utils/rdma_perf.py` (rewrite, replaces v0.2.x stub)
- `utils/rdma_handshake.py` (new)
- `utils/rdma_stream_engine.py` (new)
- `widgets/rdma_blast_flow_dialog.py` (new)
- `traffic_client/rdma_menu_actions.py` (new)
- `traffic_client/main.py` (RDMA submenu wiring)
- `widgets/stream_dialog.py` (Engine combo + RDMA params + 4 help
  guides updated)
- `traffic_client/statistics_section.py` (Engine column RDMA
  branch + stats payload forwards `engine` + `rdma`)
- `run_tgen_server.py` (8 routes + `engine=rdma` short-circuit)
- `resources/dpdk/install_dpdk.sh` (perftest in apt list)
- `install_ostg_complete.py` (`_install_rdma_userspace` helper)
- `tests/test_rdma_perf.py` (new, 16 tests)
- `tests/test_rdma_handshake.py` (new, 9 tests)
- `tests/test_rdma_stream_engine.py` (new, 15 tests)
- `tests/test_rdma_blast_flow_dialog.py` (new, 9 tests)

---

## [0.3.11] - 2026-06-03

**Operator-facing "easy DPDK + line rate" release.** Stops 0.3.10's
"how do I actually configure a line-rate blast?" pattern by adding
a one-click Blast a Flow dialog AND a 14-template library, then
polishes the Add Stream dialog end-to-end so editing a stream no
longer silently loses fields on reopen.

### What changed (by area)

**DPDK — Blast a Flow dialog (new since 0.3.10, hardened this release)**
- `widgets/dpdk_blast_flow_dialog.py` — `DEFAULT_FRAME_SIZE` 64 → 1500
  (Ethernet MTU; 100 G needs only 8.2 Mpps at MTU, well inside a
  single tx_worker core; 64 B caps ~23 Gbps single-core and was
  unreachable as a default). `DEFAULT_DST_MAC` ff:ff:ff:ff:ff:ff →
  02:00:00:00:00:02 (broadcast triggered driver-side special handling
  that capped wire rate). Layout: removed wrong column-stretch causing
  field overlap; vSpacing 6→10 + setRowMinimumHeight; pinned dialog
  setMinimumHeight to sizeHint so parent can't squeeze the group.
- `traffic_client/dpdk_menu_actions.py::show_dpdk_blast_flow_dialog`
  — `dlg.exec_()` → `dlg.show()` + `self._blast_dialogs.append(dlg)`
  + `finished.connect(prune_hook)`, enabling **parallel multi-NIC
  blasts**. Cascade-position each new dialog (`80 + 36 × index`) so
  they don't stack. Inject `siblings_iface_provider` so the dialog
  can warn before starting a second tx_worker on an already-claimed
  NIC (DPDK PMD lock contention / silent throughput halving).
- Dialog is now `Qt.NonModal` (parent class stays ApplicationModal
  for short setup flows) — operator can use the main window while
  traffic blasts.

**Stream templates (Add Stream → Template dropdown)**
- `utils/traffic_templates.py` — 6 new scaling templates: MAC sweep
  1024 dst MACs, IPv4 dst /24, IPv4 src 256, IPv6 dst 64, 5-tuple
  RSS, VLAN ID 4094. Plus `udp_line_rate_1500b` sibling of the
  existing 64 B template. Default `_DEFAULT_SRC/DST_MAC` unified
  with Blast a Flow (02:00:00:00:00:01/02 instead of
  aa:bb:cc:dd:ee:01/02) so packet captures from the two paths
  line up. Cross-module test pin enforces parity.
- `widgets/stream_dialog.py::AddStreamDialog.__init__` — Template
  dropdown was being suppressed for fresh new streams because the
  launcher passes `{"stream_id": uuid}` (non-empty dict), failing
  the `not self.stream_data` gate. Replaced with a real
  edit-existing-stream check based on operator-facing keys.
- `template_combo` gets `setMaxVisibleItems(30)` +
  `combobox-popup: 0` stylesheet so all 14 templates render in one
  popup (macOS native popup ignores maxVisibleItems by default;
  was clipping the last 5 entries below the fold).
- `populate_stream_fields` str()-coerces `frame_size` / `mpls_label`
  / `mpls_ttl` / `mpls_experimental` so templates supplying int
  values don't crash `QLineEdit.setText`.

**Add Stream dialog — Protocol Selection tab**
- Removed dead L1 group (None / MAC / RAW — value was written to
  the stream dict but no packet builder ever read it) and dead
  Payload Random radio (no encoder path).
- Second "L4" radio group relabelled **Encap (over UDP)** — was
  the same label twice in two boxes; ambiguous.
- Frame size `QIntValidator(64, 9216)` (was 1518) — jumbo frames
  now enterable. Matches tx_worker + Blast a Flow ceiling.
- Tab labels stopped eliding ("Protocol Selection" was "rotocol
  Selectio") via `min-width: 118px` stylesheet + `setExpanding(False)`.
- Dialog default height tuned to 640 px for the tallest tab
  (Protocol Data sizeHint=465 + chrome) — was 720 leaving
  ~130 px dead absorber space.

**Add Stream dialog — Protocol Data tab (round-trip + validation fixes)**
- `rocev2_use_perf_server` checkbox + Payload `payload_data_field`
  were COLLECTED but never RESTORED — operator ticked / typed,
  saved, reopened, got an empty field. Silent loss. Now both
  round-trip in populate.
- PCAP fields (`enable_pcap_checkbox`, `pcap_file_path`,
  `pcap_loop_count`, `pcap_rate_mode`) were COLLECTED but never
  RESTORED — same silent-loss bug, hit operators editing an
  existing PCAP-replay stream.
- Packet View tree's Payload row used wrong key path
  (`payload_data.data` instead of `payload.payload_data`) — custom
  payload bytes never appeared in the preview.
- RoCEv2 GID source/destination mode dropdowns weren't wired to
  enable their step/count fields; picking "Increment" left the
  sweep config unreachable.
- ARP MAC + IP fields now go through `_wire_live_validators` like
  every other MAC/IP field — was bypass.
- TCP/UDP "Override Source/Destination Port" checkboxes now clear
  the field value on uncheck (was serializing stale port despite
  override=False).
- MAC src/dst step gain `QIntValidator(1, 16M)` (reject 0/negative).
- TCP checksum hex-pair regex validator.

**Add Stream dialog — cross-layer save guards**
- New `accept()` override surfaces a "Save Anyway / Fix First"
  QMessageBox when:
  - L2=None + L3=IPv4/IPv6/ARP (no Ethernet header → NIC drops)
  - Frame Type=Random with Min ≥ Max (uniform stream or crash)
  - PCAP enabled + protocol stack picks both set (PCAP wins,
    operator's L3/L4 picks silently ignored)
- L3 transition (e.g., IPv4 → None) now resets scale-mode
  dropdowns back to "Fixed" so the next IPv4 enable doesn't show
  stale "Increment" with empty step/count.
- Template apply now fires `_refresh_packet_view_if_visible()` so
  the Packet View tab shows the templated packet immediately
  instead of stale-until-you-type.
- VLAN ID field gained `QIntValidator(1, 4094)` + tooltip.
- L3=ARP now UNCHECKS L4 radios (was only disabling the groupbox,
  leaving stale L4=UDP serialized in the saved stream).

**Add Stream dialog — compact + professional look**
- Tighter spacing pass: protocol_tab outer margins, basics row
  spacing, frame length row spacing, protocol stack vSpacing all
  reduced. QGroupBox stylesheet padding-top 10 → 4. Dialog rendered
  height shrunk 11%. `addStretch(1)` at the end of
  protocol_tab_layout absorbs leftover height into a bottom spacer
  instead of inflating the groupboxes.
- Tab styling refresh: thinner padding, selected-tab bold + blue,
  hover state, force Qt popup so all 6 tabs fit with `min-width:
  118px` instead of overflowing.

**Settings dialog crash fix**
- `traffic_client/menu_actions.py::open_settings_dialog` — opening
  it threw `OverflowError: argument 2 overflowed: value must be in
  the range -2147483648 to 2147483647` because BGP-ASN field used
  `QSpinBox.setRange(1, 4294967295)` — Qt5 QSpinBox is int32.
  Replaced with QLineEdit + QRegExpValidator + string-form storage
  in QSettings so the full 4-byte ASN range (RFC 6793) round-trips.

**RFC 2544 dialog (cross-dialog consistency + RFC compliance)**
- MAC defaults unified with Blast a Flow + Stream templates
  (`02:00:00:00:00:01/02`) — packet captures from all three line-
  rate test paths now align.
- Duration default 10 s → 60 s per RFC 2544 §26.1. Was labelled
  "fast sanity check" but operators kept exporting "RFC 2544
  Throughput Test Report" with 10-s measurements that auditors
  rejected. Tooltip now explains when to dial down.
- Loss-tolerance tooltip clarifies the formula + when to use
  non-zero tolerance.

**Devices tab polish**
- Right-click context menu was missing **Edit** — operator had to
  use the toolbar Edit button or double-click; broke parity with
  the Streams tab's right-click menu. Added between Apply and
  Copy.
- BGP local + remote ASN fields gained `QRegExpValidator(\d{1,10})`
  + tooltip explaining the 4-byte ASN range. Was apply-time-only
  range check — operator typed "abc", got a generic warning AFTER
  clicking Apply.

**API guide (Help → API Guide) updated**
- Endpoint summary now lists `/api/dpdk/hugepages`,
  `/api/dpdk/iommu`, `/api/dpdk/load_modules`,
  `/api/dpdk/interfaces`, `/api/dpdk/verify`,
  `/api/admin/bind_history`, `/api/admin/install_dpdk`.
- `/api/dpdk/hugepages` documented with the v0.3.11-corrected
  schema (`num_pages` / `page_size`, not `count` / `size_kb`).
- `/api/admin/bind_history` documented with the actual
  `{"history": {pci_bdf: {name, kernel_driver}}}` shape used by
  the Blast a Flow + Make DPDK Ready dialogs.
- `/api/streams/stats` response example extended with
  `actual_engine`, `fallback_reason`, `per_core_tx_count`,
  `engine_version` fields.
- Stop-payload callout: LIST shape `[{interface, stream_id}]`
  asymmetric with start (DICT keyed by iface) — silent no-op trap
  if confused.

**In-app help guides (Help → What's New, Help → Capabilities)**
- "0.3.11 highlights" block added at top of What's New.
- New "0. Quick-launch workflows" section in Capabilities cross-
  links Blast a Flow + template library with use-case guidance.

### Test count
- 1056 tests passing across the broader codebase (1 pre-existing
  L2-protocols failure unrelated to this release).
- 49 templates suite tests (was 15; added round-trip pins, RoCEv2,
  PCAP, packet-view-key, RFC 2544 MAC/duration, Devices Edit menu,
  BGP ASN validator).
- 108 DPDK orchestrator tests.
- 3 Settings dialog tests (new — Settings was never tested before).

### Files changed
- `widgets/dpdk_blast_flow_dialog.py`
- `widgets/dpdk_make_ready_dialog.py`
- `widgets/stream_dialog.py`
- `widgets/devices_tab.py`
- `widgets/rfc2544_dialog.py`
- `utils/traffic_templates.py`
- `traffic_client/dpdk_menu_actions.py`
- `traffic_client/menu_actions.py`
- `tests/test_templates.py`
- `tests/test_dpdk_orchestrator.py`
- `tests/test_settings_dialog.py` (new)

---

## [0.3.10] - 2026-06-02

**Server menu: new "Mark Selected Servers Offline" action.**
User-requested feature. Pre-v0.3.10 the Server menu had only
the inverse — "Make Selected Servers Online" — for reconnecting
TGs the system detected as failed. There was no operator-driven
way to mark a healthy TG as offline for quick silencing.

### What changed
- **`traffic_client/main.py`** — new `QAction("Mark Selected
  Servers Offline")` added to the Server menu next to the
  existing online-toggle action. Always enabled (handler
  validates selection).
- **`traffic_client/menu_actions.py:mark_selected_servers_offline`**
  — handler:
  - Validates that at least one server is selected. Surfaces
    `QMessageBox.information` otherwise.
  - Filters the selection to currently-online TGs. Surfaces a
    different info dialog if every selected TG is already
    offline.
  - Confirms via `QMessageBox.question` listing the affected
    addresses + the trade-offs (next health probe will flip
    them back if reachable; this is "mark", not "block
    reconnect").
  - On Yes: for each filtered server, sets `server["online"]
    = False`, appends to `failed_servers`, calls
    `update_server_status_icon(server, False)` — which
    triggers the v0.3.9 iface-children cascade so the iface
    dots agree with the parent TG state.
  - Enables `make_server_online_action` so the operator can
    flip back without restarting.
  - Refreshes the server tree so the parent LED + cascaded
    children re-render together.

### Why not persistent?
The action is "mark", not "block reconnect". The retry worker
keeps probing — if the TG is actually reachable, the next
health probe will mark it online again. Operators who want a
permanently-silent TG can use `File → Remove Server` instead.
The tooltip + confirmation dialog document this trade-off so
the behaviour isn't surprising.

### Tests
- **`tests/test_mark_servers_offline.py`** — 9 pins:
  - Action wired to the Server menu, label correct, handler
    connected.
  - Action enabled by default (no selection subscription).
  - Handler method defined.
  - No-selection path → info dialog, no confirm.
  - All-already-offline path → info dialog (different from
    no-selection one).
  - User-declines-confirm → server state unchanged + not added
    to `failed_servers`.
  - User-confirms → previously-online servers flip to offline,
    already-offline ones untouched, `update_server_status_icon`
    called per marked server with `False`.
  - User-confirms → `make_server_online_action.setEnabled(True)`
    so recovery is immediately discoverable.
  - User-confirms → `update_server_tree()` called once to
    re-render the parent LED + v0.3.9 cascade together.

### Test count
827 → 836 (+9).

## [0.3.9] - 2026-06-01

**Fix: server-tree iface child dots didn't cascade when the
server went offline.** User-reported via screenshot: the server-
tree's parent "TG 1" node showed red (offline), but the iface
children below it (lo, eno8303, eno8403, enp13s0f0np0, etc.)
kept showing their last-known green / red dots from the stale
cache. Operator saw "TG 1 offline" next to "lo ✓ up" + a mix
of greens and reds underneath — nonsense, because we can't
measure the link state of an iface on a server we can't reach.

### Root cause
`_update_server_led` only swapped the parent's status icon; the
iface child items were never touched. They kept whatever dots
`update_server_tree` painted on them from the LAST successful
`/api/interfaces` poll, so a server transitioning to offline
left the children visually stuck on stale state.

### What changed
- **`traffic_client/server_section.py:_cascade_offline_to_iface_children`**
  — new helper that walks the iface child items under a
  server's tree node and:
  - Swaps each dot to red (best-available representation of
    "we don't know; treat as unreachable").
  - Updates the tooltip to "<iface> — server offline; iface
    state unknown (last-known status is stale)" so the
    operator understands the cascade is from server
    unreachability, not a real link-down.
- **`_update_server_led`** invokes the cascade ONLY when the
  server transitions to offline. The online (green) and
  degraded (amber) branches don't touch children — the next
  successful `/api/interfaces` poll repopulates from
  authoritative state via `update_server_tree`.
- Defensive: no-op when the tree item isn't attached yet (race
  during first build) or when the server has no rendered
  children. The next rebuild fixes it regardless.

### Why not a separate "unknown" / grey icon?
There's no `grey_dot.png` asset in `resources/icons/`; adding
one + plumbing it through would be a larger change. The
operational meaning of "server offline" is "you can't use
these interfaces" — same as "the iface is down" from the
operator's perspective. Tooltip carries the semantic
distinction for anyone hovering.

### Tests
- **`tests/test_server_tree_offline_cascade.py`** — 5 pins:
  - `_cascade_offline_to_iface_children` helper exists.
  - `_update_server_led` calls the cascade AND gates it on
    `if not online:` (calling it on the online branch would
    clobber real iface up/down state with stale red).
  - Behavioural test (with `qapp` fixture): cascade swaps the
    icon on each child + sets a tooltip mentioning the iface
    name AND "server offline" AND "stale"/"unknown".
  - Cascade is a no-op when the server has no `status_item`
    (added but never rendered).
  - Cascade is a no-op when the server has 0 children (iface
    fetch hasn't completed yet).

### Test count
822 → 827 (+5).

## [0.3.8] - 2026-06-01

**L2 Emulation audit close.** Two real findings from the L2
Emulation tab audit shipped; one Tier-1 claim filtered as a
false positive (with a regression pin guarding the
verified-correct code from a future "cleanup").

### What changed

- **`_SESSIONS` no longer grows unbounded**
  (`utils/l2_protocols.py:stop_session`). Pre-v0.3.8 the
  registry dict only marked entries as stopped — they
  persisted forever. On a long-running server with many
  start/stop cycles the dict grew without bound. v0.3.8
  evicts the entry via `_SESSIONS.pop(session_id, None)`
  inside the registry lock after the worker thread joins.
  The final counters are still returned in the
  `/api/l2/<proto>/stop` response body so the client has its
  post-mortem data; keeping the in-memory entry alive added
  nothing the operator could use.

- **L2 session-start no longer freezes the GUI for up to 15 s**
  (`widgets/l2_emulation_tab.py:_on_start_clicked` + new
  `_JsonPostWorker` + new `_on_start_failed` dispatcher).
  Pre-v0.3.8 the start path called `requests.post(timeout=15)`
  synchronously on the GUI thread — a slow / unreachable
  server parked the entire client window. v0.3.8 dispatches
  the POST via a new `_JsonPostWorker` QThread (mirrors the
  existing `_JsonFetchWorker` shape — `finished_ok` +
  `failed(msg, http_code)`). The pre-existing branching for
  200 / 404 / 401-403 / generic now runs from the `failed`
  signal in `_on_start_failed`. Reuses `utils.qthread_keepalive`
  to dodge the v0.2.20–v0.2.25 SIGABRT class.

### Filtered as false positive
- **"BFD `struct.pack` format string typo"** — the audit
  flagged `"!BBBBII III"` (with a space) as suspicious. Python
  explicitly ignores whitespace in struct format strings;
  the packed output is exactly 24 bytes (RFC 5880 §4.1).
  Verified by computing the pack output. Two regression pins
  added so a future "let me clean up that space" edit can't
  silently break the wire format.

### Tests
- **`tests/test_l2_v0_3_8.py`** — 9 pins:
  - `_SESSIONS` eviction on stop_session (the memory-leak fix).
  - Unknown session id stays a no-op + False return.
  - `stop_all_sessions` drains the registry.
  - `_JsonPostWorker` class exists + has the right signal
    shape.
  - `_on_start_clicked` uses the worker (no inline
    `requests.post`) + the keepalive pin.
  - `_on_start_failed` dispatcher method exists.
  - Dispatcher branches by http_code (404, 401/403,
    `_enter_unsupported_mode`).
  - BFD `struct.pack` output is exactly 24 bytes.
  - Source format-string pin guards against accidental
    "cleanup".

### Test count
813 → 822 (+9).

### Status of L2-audit follow-ups
| Item | v0.3.8 |
|---|---|
| `_SESSIONS` memory leak | **✅ shipped** |
| 15s GUI block on Start | **✅ shipped** |
| BFD struct.pack "typo" | filtered — false positive (regression pinned) |
| LACP port priority tooltip | deferred (polish) |
| Session table default sort | deferred (polish) |

## [0.3.7] - 2026-06-01

**Three small polish fixes.** Two are deferred items from the
v0.3.4 flow-tracking audit; one is a deferred item from the
install-dialog audit. All real, all small, all closing audit
follow-ups so the deferred list shrinks.

### What changed
- **Loss% null contract on warmup-window streams**
  (`traffic_client/statistics_section.py:~1857`). Pre-v0.3.7 a
  newly-started stream with `tx_count == 0` rendered as "0.00%"
  in **green** — a false-positive "perfect zero loss" reading
  when actually no packets had been TX'd at all. Compute path
  now stores `loss_pct = None`; renderer treats None as the
  muted "—" placeholder (same as the "flow tracking off" case).
- **`.whl` extension warning on the install dialog's wheel
  picker** (`widgets/install_server_dialog.py:_browse_wheel`).
  The "All files" option in the file dialog let operators pick
  tarballs / arbitrary binaries; they'd wait 5 min for the
  upload, then pip would reject downstream. New post-pick
  `QMessageBox.warning` if the path doesn't end in `.whl`
  (case-insensitive). Warns rather than blocks — the operator
  may have a legitimate edge case.
- **Ctrl+Return shortcut on the install/upgrade dialog**
  (`widgets/install_server_dialog.py:_on_ctrl_return`). Matches
  the standard pattern from Stream dialog v0.2.96, RFC 2544
  v0.3.0, DPDK Status v0.2.97. Dispatcher checks
  `tabs.currentIndex()` and clicks the active tab's primary
  button (Upgrade tab → `up_btn`; Fresh-install tab →
  install button). No-ops if the button is disabled (mid-run).

### What didn't change
- **Test Connection button on the Upgrade tab** — the audit
  flagged this as a Tier 2 PAIN. Skipped in v0.3.7 because
  porting the Fresh-Install `_start_ssh_test` machinery
  (probes, status panel, button-state management) is closer to
  a 100-line port than a 30-line polish. Stays deferred.

### Tests
- **`tests/test_v0_3_7_polish.py`** — 7 pins:
  - Loss%-compute block stores `None` when `tx_count == 0`.
  - Renderer carries the `elif loss_pct is None: → "—"` branch.
  - Loss-formula backward compat (`(tx-rx)/tx*100` still
    present for the positive case).
  - `_browse_wheel` has the .whl check + QMessageBox warning.
  - Extension check is case-insensitive (`.lower()` + `endswith`).
  - Ctrl+Return shortcut wired in `InstallServerDialog.__init__`.
  - `_on_ctrl_return` dispatcher exists, branches by
    `currentIndex()`, and respects `isEnabled()`.

### Test count
806 → 813 (+7).

### Deferred follow-ups status
| Item | Status |
|---|---|
| Loss% null contract on idle streams | **✅ shipped** |
| Tuple-match fallback dport narrowing | still deferred |
| Auto-relax 2s timeout drop indicator | still deferred |
| OOO packet detection surfacing | still deferred |
| Install: wheel extension check | **✅ shipped** |
| Install: Ctrl+Return shortcut | **✅ shipped** |
| Install: Test Connection on Upgrade tab | still deferred |

## [0.3.6] - 2026-06-01

**Fix: "Read More: DPDK Traffic Blast Workflow" button was
invisible.** User screenshot showed a solid blue bar with no
visible text where the Read-More button should be. Real
discoverability bug — the link to the DPDK workflow guide was
effectively hidden in the most-touched dialog in the app.

### Root cause
`widgets/stream_dialog.py:~4495` set the button's text color to
`#3b82f6` (blue) but left `background-color` unspecified, so it
inherited the dialog-wide primary-button background (also a
blue). Blue text on a blue background = invisible label —
operator saw just the gradient bar.

### What changed
- **`widgets/stream_dialog.py`** — explicit `background-color:
  #ffffff` + text color bumped to `#1d4ed8` (Tailwind blue-700,
  AA-contrast-safe on white) + thin blue border + `:hover` /
  `:pressed` states + `setCursor(Qt.PointingHandCursor)`. Matches
  the "neutral white" button family used elsewhere (Stats dock
  Clear/Export, DPDK Status Unbind, install-script footer
  buttons).
- Added a small open-book emoji prefix to the label so the
  button reads as documentation-link-shaped at a glance.

### Tests
- **`tests/test_dpdk_read_more_button_visibility.py`** — 4 pins:
  - `background-color` explicitly set (the v0.3.6 fix).
  - Background must be white (#ffffff) — any colour risks the
    same readability issue.
  - Text color uses `#1d4ed8` for AA contrast.
  - `:hover` state present — without it the button doesn't
    visually respond to mouse-over.
  - `setCursor(Qt.PointingHandCursor)` present — standard
    "clickable" affordance the app uses everywhere else.

### Test count
802 → 806 (+4).

## [0.3.5] - 2026-06-01

**Per-stream latency histograms.** Second real correctness fix
from the v0.3.4 flow-tracking audit. Pre-v0.3.5 the
`LatencySampler` accumulated all NLAT-tagged packets into ONE
histogram per RX interface — two concurrent streams on the same
iface produced one mixed histogram, and the GUI joined that
mixed blob onto every stream sharing the iface. Operator saw a
single set of p50/p95/p99 numbers that wasn't meaningful for
either stream individually.

### What changed
- **`utils/latency_sampler.py:_SIG_EXTRACT_RE`** — new module-
  level regex extracts the stream_id from the
  `[<stream_id>(/q<queue>)?#<seq>]` signature the v0.3.4
  matcher already standardised. Tolerates both Scapy and DPDK
  packet formats.
- **`utils/latency_sampler.py:LatencySampler`** — gained
  `_per_stream_stats: Dict[str, LatencyStats]` + a
  `threading.Lock` protecting dict iteration. `_on_packet`
  now searches the post-NLAT-header payload for the signature
  and, when found, also adds the sample to a per-stream
  histogram. The aggregate `stats_obj` continues to be
  updated unconditionally, so backward-compatible callers
  using `.stats()` see the same numbers as pre-v0.3.5.
- **`utils/latency_sampler.py:stats_by_stream`** — new method
  returns `{stream_id: snapshot}` for every stream the sampler
  has decoded signature + NLAT for in the recent window.
- **`run_tgen_server.py:latency_stats` endpoint** — response
  body now carries a `streams: {stream_id: {...}}` field
  alongside the legacy aggregate fields. Older clients ignore
  it; newer clients prefer per-stream when present.
- **`traffic_client/statistics_section.py`** — per-stream
  lookup added to the latency join. For each stream row,
  prefer `iface_blob["streams"][stream_id]` when the server
  returned one; fall back to the iface aggregate otherwise.
  Pre-v0.3.5 GUI behaviour preserved when the server is older
  (no `streams` field) OR when `flow_tracking=off` (no
  signature → no per-stream bucket).

### When this kicks in
The per-stream path requires BOTH:
- `capture_latency=True` (NLAT header in TX packets)
- `flow_tracking=True` (signature in TX packets)

This was already a documented prerequisite — pre-v0.3.5 the
combination just silently produced a per-iface mixed
histogram. Operators with only one of the flags on see the
same behaviour as before.

### Tests
- **`tests/test_latency_per_stream.py`** — 13 pins:
  - Extractor regex captures stream_id for Scapy (`[sid#seq]`)
    and DPDK (`[sid/q<n>#seq]`) formats.
  - Extractor doesn't match unsigned packets (capture_latency-
    only mode stays clean).
  - Extractor rejects malformed `/q` segments.
  - `_on_packet` populates per-stream buckets when signature
    is present, with correct sample counts.
  - Aggregate `.stats()` still counts all samples (backward
    compat).
  - Unsigned packet lands in aggregate but no per-stream
    bucket.
  - Two streams' samples on same sampler end up in two
    separate buckets — the actual bug fix.
  - Threaded concurrent-insert stress: 6 workers × 50 samples
    each + concurrent `stats_by_stream()` reads must not raise.
  - `.stats()` signature unchanged — pin the legacy dict shape
    so old GUIs don't break.

### Test count
789 → 802 (+13).

### Status of the v0.3.4 audit follow-ups
| Item | v0.3.5 |
|---|---|
| Per-stream latency histogram | **✅ shipped** |
| Loss% null contract on idle streams | deferred |
| Tuple-match fallback ignores per-stream dport | deferred |
| Auto-relax 2s timeout masks early drops | deferred |
| Out-of-order packet detection not surfaced | deferred |

## [0.3.4] - 2026-06-01

**Flow-tracking: fix silent zero RX count on DPDK streams.**
Real correctness bug surfaced by the v0.3.4 flow-tracking audit:
the RX sniffer's signature matcher was a fixed prefix
(`f"[{stream_id}#".encode()`) that recognised only the Scapy TX
packet format and silently ignored the DPDK TX format. Any
stream with `flow_tracking=True` AND `dpdk_enable=True` showed
`rx_count=0` / `loss=100%` in the GUI — operator saw "none of
my DPDK packets got through" when they actually all arrived;
the sniffer just couldn't recognise them.

### Root cause
The two TX backends embed different signatures in each packet:

- **Scapy TX** (`multithreaded_traffic_gen.py:_append_sig_with_seq`):
  `[<stream_id>#<seq>]`
- **DPDK TX** (`resources/dpdk/tx_worker/tx_worker.c:223`):
  `[<stream_id>/q<queue_id>#<seq>]`

The DPDK side embeds the per-queue ID for debug visibility. The
RX sniffer only cares about the `<stream_id>`, but its matcher
demanded `#` immediately after the stream_id — the optional
`/q<queue_id>` segment broke it. Bug pre-dates v0.2.94 and
has been in tree since DPDK TX gained per-queue tagging.

### What changed
- **`multithreaded_traffic_gen.py:_build_sig_pattern`** — new
  module-level helper that compiles a bytes regex tolerating
  the optional `/q\d+` segment:
  `re.compile(rb"\[" + re.escape(stream_id.encode()) + rb"(?:/q\d+)?#")`
  Extracted out of the sniffer-thread closure so the pattern
  has its own tests without spinning up scapy.
- **`multithreaded_traffic_gen.py:start_rx_counter`** — the
  closure-local `sig_prefix` byte-string is replaced with a
  call to `_build_sig_pattern(stream_id)`. The `_sig_present`
  helper now does `pat.search(raw_bytes)` instead of `in`.
- `stream_id` is `re.escape`'d so legitimate IDs containing
  regex meta-characters (dots, parens, plus signs — UUIDs use
  hyphens; some installs use dot-separators) match as literals
  instead of being treated as wildcards.

### Tests
- **`tests/test_flow_tracking_sig.py`** — 18 pins (mostly
  parametrised):
  - Returns a compiled bytes regex.
  - Matches every Scapy-format example (6 cases inc. UUID-
    style and dot-separator IDs).
  - Matches every DPDK-format example (7 cases inc. multi-
    queue 0/1/15/127 and dot-separator IDs).
  - Does NOT match similar-looking other-stream signatures
    (prefix-collision, missing `[`, missing `#`).
  - Does NOT match malformed `/q` segments (no digits,
    letters, full word).
  - `re.escape`'s the stream_id (the test for `s.x` not
    matching `sax`).
  - Searches within larger payloads (real packets have
    padding before/after the signature).

### Other flow-tracking audit findings — documented in
### "Unreleased — known follow-ups" below
None of the other 5 PAIN findings ship in v0.3.4. Each is real
but design-level work bigger than a focused patch release:
- Loss% returns `0.0` on idle streams instead of null (GUI
  handles correctly with `if tx > 0` guard; API contract
  ambiguity).
- RX matching falls back to L2/L3/L4 tuple when signature
  missed — design tradeoff for missed-signature recovery.
- Auto-relax 2 s timeout can mask early frame drops.
- Latency histogram is per-interface, not per-stream
  (two concurrent streams on same RX iface have mixed
  samples).
- Out-of-order packet detection is computed but never
  surfaced in the GUI.

### Test count
771 → 789 (+18).

## [0.3.3] - 2026-06-01

**CI: opt every release job into Node.js 24.** GitHub deprecated
Node.js 20 on 19 Sep 2025 and started forcing Node.js 24 on
16 Jun 2026 for JavaScript actions. Every release run since v0.2.94
has been logging deprecation warnings:

> ⚠ Node.js 20 actions are deprecated. The following actions are
> running on Node.js 20 and may not work as expected:
> actions/checkout@v4, actions/setup-python@v5,
> actions/upload-artifact@v4, actions/download-artifact@v4,
> softprops/action-gh-release@v2.

### What changed
- `.github/workflows/release.yml` — added a workflow-level
  `env: FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"` block. This
  is the opt-in path GitHub explicitly documents in the
  deprecation notice. It flips the runtime engine for every
  JavaScript action in the workflow without needing per-action
  major-version bumps.

### Why not bump individual action versions?
The alternative was upgrading each action to a Node-24-compatible
major (e.g. `actions/checkout@v5`). That introduces API-shape
risk per action (new defaults, removed options) and would have
required individually verifying each release-pipeline asset still
builds correctly. The env-var path is purely a runtime-engine
selector — it doesn't change any action's API surface, so if it
breaks anything, the surface area to debug is narrower.

If a specific action turns out to be incompatible with Node 24,
the fallback is per-action version bump for that single action.
None of the pinned actions have had reported Node 24
incompatibilities to date.

### Tests
None — purely a CI policy change. Verification: the next release
run won't log the Node 20 deprecation warning, and all 4
artifacts still publish cleanly.

### What's deferred (from the v0.3.2 audit cycle close)
- **C-side tx_worker audit** — agent flagged one "BLOCKER"
  (uninitialised `pkts[]` array) which inspection confirmed as a
  false positive: when `rte_pktmbuf_alloc_bulk` fails, the
  loop `continue;`s and skips the free path entirely. The free
  loop only walks indices written by a successful alloc. No
  fix shipped.
- Other deferred items (async progress dialogs, sortable
  protocol-table headers, last-error column, etc.) unchanged
  from the v0.3.2 list.

## [0.3.2] - 2026-06-01

**DPDK closing-pass.** All four items the v0.3.1 audit cycle
deferred — the tx_worker stdout deadlock + the three shell-script
PAINs — landed together. The "Unreleased — known follow-ups"
section that documented these has been trimmed accordingly; only
the genuinely-still-deferred items remain.

### What changed
- **tx_worker stdout reader: blocking iteration → select-based
  poll** (`utils/dpdk_tx_worker.py`). Pre-v0.3.2 the launcher
  thread read tx_worker output with `for line in proc.stdout:`
  — a blocking iterator that could park the thread indefinitely
  if DPDK EAL hung on device initialisation. Operator had to
  restart the server to recover. New reader runs `select.select(
  [stdout_fd], [], [], 0.5)` in a `while True:` loop that
  checks `stop_event.is_set()` AND `proc.poll()` between each
  500 ms timeout, so an operator-initiated stop OR a clean
  child exit both wake the loop within half a second.
  Line-processing logic was extracted into a `_process_one_line`
  inner closure so the same dispatch handles both the main
  loop and the drain-on-exit path.
- **PCI address regex validator in `dpdk_bind.sh`**
  (`resources/dpdk/dpdk_bind.sh:validate_pci_address`). Both
  `bind_to_dpdk` and `unbind_from_dpdk` now call it before any
  sysfs write. Defence-in-depth complement to the v0.3.1
  `_is_safe_iface_name` whitelist: the Flask layer's PCI check
  is `":" in pci`, which is too permissive. Regex matches the
  kernel-standard format `<NNNN>:<NN>:<NN>.<N>` (hex).
- **`/tmp/dpdk_deps_install.log` written under `umask 077`**
  (`resources/dpdk/install_dpdk.sh`). Subshell-scoped
  `(umask 077 && eval … | tee …)` so the temp log ends up 0600
  instead of the default 0644. Outer process umask is
  unchanged.
- **`git clone` artifact validation in `install_dpdk.sh`**
  (`resources/dpdk/install_dpdk.sh`). After the DPDK clone +
  checkout, the script now verifies `meson.build` exists and
  `lib/` is a directory. A corrupted clone used to surface 60 s
  later as a cryptic meson error; now it fails at the source
  step with an actionable message ("delete $DPDK_DIR and
  re-run").

### Why this isn't a feature release
All four items are correctness + hardening. No new user-visible
features. v0.3.2 is the natural patch-bump close on the
v0.3.1 DPDK audit cycle.

### Tests
- **`tests/test_dpdk_v0_3_2.py`** — 9 pins:
  - tx_worker imports `select` + uses `select.select(`.
  - Blocking `for line in proc.stdout:` removed; check both
    `stop_event.is_set()` AND `proc.poll()` exist in the new
    reader window.
  - `dpdk_bind.sh:validate_pci_address` function defined +
    parametrised test confirms both `bind_to_dpdk` AND
    `unbind_from_dpdk` call it.
  - Actual shell-extract test runs the validator against a
    matrix of good (4) + bad (9) PCI strings.
  - `install_dpdk.sh` tee runs in `(umask 077 && ...)`
    subshell.
  - Post-clone block verifies `meson.build` exists.

### Test count
762 → 771 (+9).

## Unreleased — known follow-ups

Items still on the deferred list — not blocking, but documented
so a future session knows they exist.

### From the v0.3.4 flow-tracking audit
- **RX matching falls back to L2/L3/L4 tuple when signature
  missed** (`multithreaded_traffic_gen.py:_tuple_match`). Two
  streams on the same iface with identical 5-tuple but
  different signatures will get mis-attributed if the
  signature path fails. Design tradeoff for missed-signature
  recovery — fix would require per-stream dport narrowing in
  the tuple path too.
- **Auto-relax 2 s timeout can mask early frame drops**
  (`multithreaded_traffic_gen.py:~625`). If the RX sniffer
  doesn't see matching packets in 2 s it relaxes to "any UDP."
  Frames arriving after relaxation count toward `rx_count`,
  but the operator has no indicator the stream was silent at
  first. Fix needs a "first signature-matched packet seen at"
  timestamp surfaced in the stats payload.
- **Out-of-order packet detection is computed but never
  surfaced** (`multithreaded_traffic_gen.py:~475`). The
  sniffer can decode sequence numbers but no OOO counter
  exists in `update_rx()` or the API response. Feature
  appears half-finished.

### From earlier audits (still deferred)
- **Async-worker progress dialogs** for ISIS / VXLAN / DHCP
  apply paths (BGP + OSPF have them). Skipped per v0.2.93
  because moving sync applies to async worker threads risks
  reintroducing the v0.2.20–v0.2.25 QThread SIGABRT class.
- **Sortable headers** on OSPF / IS-IS / VXLAN / DHCP tables.
  Feature add, not a bug. v0.2.92 sort-state helper is ready.
- **Last-error column** on protocol tables. POLISH, deferred
  v0.2.74; internal `_apply_error` is captured but not surfaced.
- **Tunable stats-refresh interval** (currently hardcoded 2000ms
  in `traffic_client/main.py:411,430`). Needs a Settings dialog
  surface; the v0.2.99 pause toggle covers the screenshot use
  case which was the main driver.
- **Node.js 24 CI migration**. Every release run logs Node 20
  deprecation warnings. GitHub forces Node 24 on 16 Sep 2026.
  ~5-line edit when convenient.
- **Stream dialog POLISH**: Preview button, Clone, Reset,
  stylesheet unification, semantic-hint tooltips. None
  blocking.

These are *known and accepted* — they're listed here, not
hidden. Pick any up when there's a driver (bug report, feature
ask, customer complaint) or when consolidating the audit
findings warrants a focused PR.

## [0.3.1] - 2026-06-01

**Server-side DPDK hardening.** The v0.2.76 / v0.2.77 / v0.2.97
work hardened the CLIENT-side DPDK dialog (bind-safety override,
hugepage feedback, unbind confirmation). The v0.3.1 audit closed
the complementary SERVER-side gaps that the dialog can't protect
against.

### What changed
- **Concurrent bind/unbind serialised**
  (`run_tgen_server.py:_DPDK_BIND_LOCK`). Two parallel
  `/api/dpdk/bind` requests targeting the same PCI device used
  to race `dpdk_bind.sh` — one's unbind step could collide with
  the other's bind step, leaving sysfs with a NIC bound but the
  kernel-driver symlink dangling. New module-level `Lock()`
  wraps the subprocess.run on both `dpdk_bind` and `dpdk_unbind`
  handlers so the second request blocks until the first
  completes (or the 30s/60s timeout fires). Low probability in
  practice but the fix is trivial and the failure mode is
  painful enough to warrant guarding.
- **Hugepage subprocess timeouts + rollback**
  (`run_tgen_server.py:dpdk_hugepages`). Pre-v0.3.1 the
  `mountpoint` / `mkdir` / `mount` subprocess calls had no
  timeout — a stuck mount (e.g. an autofs lookup or a network
  FS in the way) would park the Flask worker thread
  indefinitely. All three now carry `timeout=10`. AND: if the
  mount fails after the sysfs allocation succeeded, the handler
  now rolls back by writing `0` to `nr_hugepages` — pre-v0.3.1
  the pages stayed reserved-but-unmounted and the operator saw
  "success" in the response while every subsequent stream-start
  failed with the cryptic "no free hugepages."
- **Iface-name input whitelist**
  (`run_tgen_server.py:_is_safe_iface_name` +
  `_get_pci_from_interface`). The audit flagged
  `_get_pci_from_interface` as a path-traversal vector. On
  inspection the actual disclosure surface was tiny (sysfs
  kernel-collapses `..` and the function only returns
  `os.path.basename(os.readlink(...))` — no file contents
  leak), but rejecting non-conforming names earlier is cheap
  defence-in-depth and any future call site that uses `iface`
  in a less-tolerant context (subprocess argv, `os.path.join`)
  is now pre-protected. Whitelist:
  `^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$` (IFNAMSIZ-1 = 32; same
  characters Linux netdev names actually use, including
  Solarflare / bonding `:` subifaces).

### Audit findings filtered (not shipped)
- **"Path-traversal can read /etc/shadow"** — over-claim. Sysfs
  collapses `..` such that `/sys/class/net/../../etc/shadow/device`
  resolves to a non-existent path; even if it existed, the
  function returns only the basename of a readlink target, not
  file contents. Reclassified from BLOCKER to POLISH input
  hygiene (still fixed, just not the panic-level fix the audit
  framed).
- **"Bind safety not on unbind"** — second time this has been
  flagged (also in the v0.2.97 audit). Unbind from vfio-pci →
  kernel driver RESTORES networking; the actual risk is just
  disrupting in-flight DPDK traffic. The v0.2.97 confirmation
  dialog covers that on the client side. Server-side `/unbind`
  intentionally has no safety check so the GUI's "Unbind anyway"
  recovery path always works.
- **"tx_worker stdout deadlock"** — real (the `for line in
  proc.stdout:` loop has no per-line timeout), but the failure
  mode is "operator restarts the server", not data loss. Fix
  would require switching from blocking line iteration to a
  select-based reader — larger refactor, deferred.

### Tests
- **`tests/test_dpdk_server_hardening.py`** — 9 source-grep +
  pure-function pins:
  - `_DPDK_BIND_LOCK` defined as a module-level `Lock()`.
  - Both `dpdk_bind` and `dpdk_unbind` handlers wrap their
    subprocess.run in `with _DPDK_BIND_LOCK:` (parametrised).
  - `_is_safe_iface_name` + `_IFACE_NAME_RE` defined.
  - `_get_pci_from_interface` calls the whitelist BEFORE
    constructing the sysfs path.
  - Whitelist accepts real names (eth0, enp181s0f0np0,
    bond0.10, em1:0, lo, wlan0, br-1234abcd).
  - Whitelist rejects path-traversal / shell-meta / oversized
    / leading-dot+dash names.
  - Every `subprocess.run` in `dpdk_hugepages` carries
    `timeout=`.
  - Rollback path writes `0` to `nr_hugepages` on mount
    failure.

### Test count
753 → 762 (+9).

## [0.3.0] - 2026-06-01

**Minor bump to mark the audit cycle close.** v0.2.93 → v0.2.99
shipped 7 consecutive patches that closed UX/safety audits across
5 major surfaces (Stateful TCP, Devices tab, Stream dialog, DPDK
Status, Statistics dock) + reclaimed 1.17 GB of repo bloat + made
the fork's CI free forever. v0.3.0 closes the cycle with the last
unaudited customer-facing surface: the **RFC 2544 wizard**, which
produces the HTML reports customers actually read.

### What changed
- **Close-while-test-running orphan fix**
  (`widgets/rfc2544_dialog.py:closeEvent / reject / accept`).
  Pre-v0.3.0 the dialog had no override on any of the three
  close paths (X button, Esc, Close button). Hitting any of them
  while a test was running silently:
  1. Orphaned the server-side test (it kept running to
     completion with no client visibility, polluting the line).
  2. Leaked the QTimer (it kept polling indefinitely until GC).
  New unified gate: if `_is_test_running()` is True, prompt to
  cancel; on Yes, fire `/api/rfc2544/stop` AND tear down the
  poll timer. Single `_stop_test_and_cleanup_timer` helper so
  the three overrides can't drift.
- **PASS / FAIL per-frame column in the HTML report**
  (`widgets/rfc2544_dialog.py:build_rfc2544_html_report`). The
  results table now leads with a coloured verdict badge: PASS
  (green) when `max_no_drop_pps > 0` (the RFC 2544 binary search
  found a passing rate within `target_loss_pct`), FAIL (red)
  when zero (no rate stayed within the loss target). Plus a
  summary line — `N/M frame sizes passed (target loss ≤ X%)` —
  so customers don't have to interpret the loss column to read
  the verdict.
- **Live MAC + IPv4 validators**
  (`widgets/rfc2544_dialog.py:_wire_live_validators`). Reuses
  the v0.2.96 `utils/stream_input.py` pure-function validators.
  Red border + tooltip on bad input as the operator types.
  `_on_start` also re-runs the same checks as a backstop so an
  operator who ignores the red border still gets blocked before
  the request hits the server.
- **Ctrl+Return shortcut to Start Test** on the dialog. Matches
  the rest of the app's modal convention.

### What didn't change
- The server-side `_rfc2544_run_step` worker, throughput math
  (`(fs + 20) * 8 / 1e9` — RFC 2544 Appendix B preamble + IFG),
  or the binary search itself. The audit cross-checked the math
  and found it correct.
- The v0.2.74 timestamped export filename — present, no
  regression.

### Audit cycle summary (v0.2.93 → v0.3.0)
| Tag | Surface | Tests |
|---|---|---|
| v0.2.93 | Re-shipped after public-flip | — |
| v0.2.94 | Stateful TCP — 3 PAIN fixes | +5 |
| v0.2.95 | Devices-tab POLISH (5 sub-tabs + OSPF validators) | +6 |
| v0.2.96 | Stream dialog input safety + Ctrl+Return | +57 |
| v0.2.97 | DPDK unbind safety + UX polish | +6 |
| v0.2.98 | CI: macOS .dmg on every tag (public-fork win) | — |
| v0.2.99 | Stats dock sort + filter/pause/last-refresh | +9 |
| **v0.3.0** | **RFC 2544 wizard close-gate + PASS/FAIL + validators** | **+15** |

**Cumulative:** +98 new tests, ~+2400 lines of net change,
roughly 6-8 audit findings filtered as false positives per
cycle (always verified against actual code before scoping).

### Tests
- **`tests/test_rfc2544_v0_3_0.py`** — 15 tests:
  - 3 parametrised pins for the closeEvent / reject / accept
    overrides (each must check `_is_test_running` AND call
    `_stop_test_and_cleanup_timer`).
  - 1 pin for the cleanup helper itself (stops timer + posts
    `/api/rfc2544/stop`).
  - 1 pin for `_is_test_running` using `isActive()`.
  - 4 pins for live-validator wiring + import + backstop.
  - 1 pin for Ctrl+Return.
  - 5 behavioural tests for the HTML builder PASS/FAIL +
    summary line (mixed pass/fail, all-pass, all-fail, target-
    loss in summary, empty rows safe).

### Test count
738 → 753 (+15).

### What's deferred
- "Resume from saved test config" (`QSettings` persistence) —
  feature, not regression; not blocking.
- Result table sortability in the GUI — minor UX, not driving
  customer pain.
- Server-side frame_size range validation — not blocking (the
  defaults clamp correctly); separate scope.

## [0.2.99] - 2026-06-01

**Statistics dock — sort-state preservation + 3 PAIN polish items.**
The audit found one real correctness gap (sort indicator wiped on
every 2 s refresh) and three operator-friction items. All four
shipped together since the wiring lives in the same action-bar /
update-method region.

### What changed
- **Sort indicator survives the 2-second refresh**
  (`traffic_client/statistics_section.py:update_stream_statistics_table`).
  The stream-statistics table has `setSortingEnabled(True)` at
  construction (line 509) but the periodic rebuild was clearing it
  via `setRowCount(0)` + per-row `setItem` — Qt was re-sorting on
  EVERY setItem call, scrambling the operator's chosen column
  order. Same `capture_sort_state` / `restore_sort_state` helper
  the Devices tab adopted in v0.2.92.
- **Filter box** above the table
  (`statistics_section.py:setup_traffic_statistics_section`). New
  `QLineEdit` with `Filter streams…` placeholder. Hides rows whose
  Stream Name / Interface / Engine cells don't contain the
  case-insensitive substring. Important for sessions with 50+
  streams where scrolling-by-sort isn't enough.
- **Pause-refresh toggle button**
  (`statistics_section.py`). Checkable button: click → freezes
  both stats tables (label flips to `Resume`, amber background).
  The polling timers in main.py keep firing server-side (cheap),
  but the GUI rebuild is gated on `_refresh_paused` at the top of
  both `update_statistics_table` and `update_stream_statistics_table`
  so the snapshot the operator screenshots stays stable.
- **"Last refresh" chip** in the action bar
  (`statistics_section.py:_update_last_refresh_chip`). Updated to
  `Updated HH:MM:SS` at the end of every successful
  (non-paused) rebuild. Lets the operator distinguish a stuck
  reading from a slow stream — pre-v0.2.99 there was no way to
  tell whether a flat-line was fresh or a poll wedge.

### Verified-shipped (not changed; cross-checked by the audit)
- v0.2.77 engine fallback badge ("Scapy ⚠ (was DPDK)") — present.
- v0.2.78 SR-MPLS badge — the audit initially flagged it as
  "missing" but searched the wrong file. It correctly lives in
  `traffic_client/server_section.py:855` (Server-Section Details
  cell), not the per-stream stats table. No regression.
- QThread keepalive pin on `StatisticsFetchWorker` — present.
- Live-chart `deque(maxlen=300)` bound — present (no memory leak).

### Tests
- **`tests/test_statistics_dock_polish.py`** — 9 source-grep +
  pure-function pins:
  - Sort-state helpers imported.
  - `update_stream_statistics_table` orders capture → setRowCount(0)
    → restore correctly.
  - Both update paths bail when `_refresh_paused`.
  - Pause-check precedes capture so we don't waste cycles on
    paused frames.
  - Action bar carries the three new widgets.
  - Helper methods defined.
  - Pause toggle flips between Pause / Resume strings.
  - Filter walks columns (0, 1, 2) — pinned so a column reorder
    must update the filter wiring too.
  - Sort-state helpers round-trip without raising on garbage
    input.

### Deferred POLISH
- Tunable refresh interval (currently hardcoded 2000ms in
  `main.py:411,430`). Would need a Settings dialog surface; the
  pause toggle covers the screenshot use case which was the main
  driver. Bigger scope than this release.

### Test count
729 → 738 (+9).

## [0.2.98] - 2026-06-01

**CI: ship macOS .dmg on every release.** The `.0`-only gate
added in `3190ef9` (Apr 2026) was a cost-control measure while
the fork was private — macOS runners are 10× the Linux per-minute
rate, and every patch release was burning ~$1.20 in macOS time
alone. After the public-flip the original cost argument no longer
applies: standard-runner compute is free and unlimited on public
GitHub repos for Linux, Windows, AND macOS. Mac operators
shouldn't have to wait for the next `.0` minor bump to get the
latest installer when the build is free.

### What changed
- **`.github/workflows/release.yml`** — removed the
  `if: endsWith(github.ref, '.0') || workflow_dispatch` gate on
  the `build-macos` job. Every tag push now builds + publishes
  a .dmg alongside the wheel, .exe, and .AppImage.
- **`.github/workflows/release.yml`** — updated the comment on
  the `release:` job's `needs.build-macos.result == 'skipped'`
  allowance to explain that it's now a defensive holdover (in
  case the gate ever comes back) rather than the expected path.

### Trade-off
- **Wall-time**: release end-to-end goes from ~3 min to ~5 min
  (macOS becomes the slowest job in the parallel set, ~10-15 min
  on a cold runner). Cancellation-on-newer-push concurrency group
  still in place so a burst of patch tags only races the last one.
- **Cost**: $0 on the public fork. Would be ~$1.20/release if the
  fork ever goes private — restoring the gate is a one-line edit.

### Verification
This release (v0.2.98) is the first patch tag in this stream to
ship .dmg. If macOS .dmg lands in the Releases page artifacts
alongside the other 3, the workflow change is verified end-to-end.

## [0.2.97] - 2026-06-01

**DPDK Status dialog — unbind safety + UX polish.** The v0.2.97
audit found one real safety gap and three smaller UX items on the
DPDK Status surface. Worth noting up front: the audit's initial
"unbind = host lockout" framing was overblown — unbinding from
vfio-pci RESTORES kernel networking; the real risk is killing
any in-flight DPDK traffic stream that was using the interface.
Still worth a confirmation prompt.

### What changed
- **Unbind confirmation gate**
  (`traffic_client/dpdk_menu_actions.py:_perform_unbind`).
  Pre-v0.2.97 both the inline Unbind button in the Status dialog
  AND the Tools → Unbind menu fired straight at the worker —
  operator could one-click their way into stopping a running
  DPDK stream. New `QMessageBox.question` gate, scoped to the
  single entry point so both surfaces are covered. Skipped when
  `is_unbound` is True (the device is already released and the
  operation is just a recovery-restore of the kernel driver — no
  in-flight traffic to disrupt; an extra prompt would be friction).
- **Inline Unbind button tooltip**
  (`traffic_client/dpdk_menu_actions.py:show_dpdk_status`). The
  red-border button now carries an explicit tooltip naming the
  consequence ("any DPDK traffic currently running on this
  interface will stop") so the operator sees it before the
  confirmation dialog even fires.
- **Empty-interfaces state message**
  (`traffic_client/dpdk_menu_actions.py:_format_dpdk_status`).
  When the server returns zero interfaces (typically because
  `dpdk-devbind.py` is missing or returned nothing) the dialog
  used to render a silent empty section that operators
  interpreted as "all good." Now emits a user-facing hint
  pointing at the tooling-failure hypothesis.
- **Ctrl+Return shortcut to dismiss** the Status dialog
  (`traffic_client/dpdk_menu_actions.py:show_dpdk_status`).
  Matches the rest of the app's modal convention; saves a mouse
  trip when the operator is in repeat-check mode chasing a stuck
  bind.

### What didn't change (audit findings verified-shipped)
The audit cross-checked v0.2.76 / v0.2.77 wiring and confirmed
none of it has regressed:
- "Bind anyway" override path (server returns 409 → client offers
  the explicit override button) — present, line 1486-1509.
- Hugepage allocation feedback toast — present.
- Async QThread keepalive pin — present.
- Periodic refresh + last-refresh-time chip — present.
- Per-core TX stats plumbing — present on the resolver side; the
  audit's claim that the Status dialog should display per-queue
  PPS is the wrong architectural choice (those stats belong in
  the Stream Stats table, where they already live).

### Tests
- **`tests/test_dpdk_unbind_safety.py`** — 6 source-grep pins:
  - `_perform_unbind` carries a `QMessageBox.question` before
    the worker dispatch.
  - Confirmation is gated on `if not is_unbound:` so recovery
    operations skip the prompt.
  - Confirmation cancellation hits a `return` (worker doesn't
    fire on No).
  - Inline Unbind button has a `setToolTip` call.
  - `_format_dpdk_status` has an `else` branch on the empty-
    interfaces case with the "No interfaces detected" hint.
  - `show_dpdk_status` wires a `QShortcut` for `Qt.Key_Return`.

### Test count
723 → 729 (+6).

## [0.2.96] - 2026-06-01

**Stream dialog — input safety + UX consistency (5 audit items
closed).** The Add-stream / Edit-stream modal is the most-touched
dialog in the app — every traffic flow starts there. The v0.2.96
audit found that while every numeric field was guarded by
`QIntValidator` at type-time, three submit-time gaps let bad data
through to the server:

- **No custom `accept()` override** — Save fired straight through
  to `QDialog.accept`, no pre-submit validation pass.
- **MAC fields were plain QLineEdit** — accepted unicode,
  garbage, the empty string. Server rejected with a less-friendly
  error after the dialog was already gone.
- **IPv4 / IPv6 fields were plain QLineEdit** — same story.
- **No cross-field check** for the frame-size group: an operator
  could enter `min=1518, max=64` and the dialog accepted it.
- **No `Ctrl+Return` shortcut to Save** — every other dialog in
  the app supports it.

### What changed
- **New `utils/stream_input.py`** pure-function validators —
  `validate_mac`, `is_zero_mac`, `validate_ipv4`, `validate_ipv6`,
  `validate_frame_sizes`, `collect_errors`. Pure-Python so they
  have their own tests without spinning up Qt. Matches the
  `utils/isis_net.py` (v0.2.86) and `utils/ospf_area.py` (v0.2.87)
  shape.
- **`widgets/stream_dialog.py:_wire_live_validators`** — wires
  `textChanged` on src/dst MAC + IPv4 + IPv6 fields to apply the
  v0.2.95 OSPF-dialog red-border stylesheet pattern. Tooltip
  carries the explanatory error so the operator can hover to see
  why a field went red. Defensive against missing attributes
  (lazy-loaded protocol sub-sections won't crash the wiring).
- **`widgets/stream_dialog.py:accept`** — custom override that
  walks every required MAC + IP field, applies the cross-field
  frame-size check, collects every error into a single
  `QMessageBox.warning` (so the operator sees the full picture
  instead of dismissing one popup at a time), and returns without
  closing the dialog on failure. Pre-v0.2.96 the buttons.accepted
  signal connected straight to `QDialog.accept` so Save closed
  the modal regardless of field content.
- **`widgets/stream_dialog.py` Ctrl+Return shortcut** —
  `QShortcut(Qt.CTRL+Qt.Key_Return)` bound to `accept()` so
  keyboard-driven operators get the same "Submit" muscle memory
  the main window already trains.

### What didn't change
- **All existing `QIntValidator` wiring** (port, MAC count, MPLS
  label, VLAN, TTL, intervals, etc.) — those already enforce
  per-field bounds at type-time. The v0.2.96 audit initially
  flagged "missing live validators on port fields" as PAIN, but
  inspection confirmed the existing `QIntValidator` already
  catches out-of-range numerics — false positive.
- **`populate_rx_ports`** — the audit flagged it as a "GUI thread
  block" but reading the method confirmed it's an in-memory walk
  of the already-resolved `server_interfaces` list, no network
  call. False positive.

### Deferred POLISH
- "Preview frame" button on the dialog (v0.2.84 L2-emulation
  pattern, but the Stream dialog's frame builder is more complex
  — needs its own design pass).
- "Clone stream" affordance (would need a template-management
  surface, larger than a polish PR).
- "Reset to defaults" button (needs to track the factory state
  per protocol section).
- Tooltips sweep for DSCP / ECN / MPLS-EXP semantic hints.
- Stylesheet unification (would touch many files; risk vs. value
  unclear without a real UI complaint).

### Tests
- **`tests/test_stream_input.py`** — 57 new pure-function tests
  covering MAC / IPv4 / IPv6 / frame-size / batch-collect across
  the obvious garbage inputs (unicode, wrong family, out-of-range,
  inverted min/max, etc.). The whole file runs in 0.07 s without
  Qt.

### Test count
666 → 723 (+57).

## [0.2.95] - 2026-06-01

**Devices-tab POLISH sweep — cross-sub-tab keyboard/RMB consistency
+ OSPF dialog live validators.** Three POLISH items deferred from
v0.2.74 / v0.2.85 closed in one release. No PAIN-tier items shipped —
the two flagged by the v0.2.94 audit (DHCP MultiDeviceResultsDialog,
ISIS kick_refresh) were both confirmed as false positives on closer
inspection (`apply_dhcp_pools` is single-device; the outer
`apply_isis_configurations` wrapper already calls kick_refresh).

### What changed
- **Delete-key + right-click context menu on all 5 protocol sub-tabs**
  (`utils/devices_tab_{bgp,ospf,isis,vxlan,dhcp}.py`). The
  v0.2.74-era pattern that landed on the main Devices table now
  fires on BGP, OSPF, IS-IS, VXLAN, and DHCP. Delete-key invokes
  each protocol's existing delete handler
  (`prompt_delete_bgp` / `prompt_delete_ospf` / `prompt_delete_isis`
  / `delete_selected_vxlan_tunnels` / `delete_selected_pool`).
  Right-click menus offer the per-protocol Refresh + Apply +
  Delete-selected actions. Shortcut is scoped to the table widget
  (`Qt.WidgetShortcut`) so an inline-edit Delete on a single char
  doesn't trigger the row-delete.
- **OSPF add dialog gains live validators**
  (`widgets/add_ospf_dialog.py`). Area-ID, Router-ID, and the
  Hello/Dead intervals now go red as the operator types invalid
  values instead of waiting for the submit-time QMessageBox. Hello
  + Dead carry `QIntValidator(1, 65535)`; the cross-field
  "Dead > Hello" constraint flags both inputs and explains itself
  in tooltips. Area-ID reuses the v0.2.87 `validate_ospf_area_id`
  helper so dotted-decimal and 32-bit-int formats both pass.

### Notes on the false positives
The Devices-tab audit produced two PAIN findings that turned out to
be already-handled:
- DHCP "missing MultiDeviceResultsDialog" — `apply_dhcp_pools`
  operates on a single selected DHCP server row, not a fan-out, so
  the dialog (designed for N-device per-row results) doesn't
  apply. `QMessageBox.information` is the correct UX here.
- ISIS "missing kick_refresh" — the inner `_apply_isis_to_devices`
  doesn't call kick_refresh, but the outer wrapper
  `widgets.devices_tab.DevicesTab.apply_isis_configurations` does
  (line ~2370), so the preflight bar gets kicked regardless. An
  explanatory comment was added at the apparent gap so the next
  audit doesn't re-flag the same false signal.

### Tests
- **`tests/test_devices_tab_audit.py`** — 6 new tests, all pinned
  with the `v0_2_95` audit-trail prefix in the docstring:
  - 5 parametrised tests (one per sub-tab) verifying the
    Delete-key shortcut + CustomContextMenu policy + the delete
    handler are reachable from the setup body.
  - 1 test verifying the OSPF dialog defines the three live-
    validator methods, wires `textChanged` to them, and carries
    `QIntValidator(1, 65535)` on the two interval fields.

### Test count
660 → 666 (+6).

## [0.2.94] - 2026-05-31

**Stateful TCP tab — UX-consistency sweep (3 PAIN gaps closed).** The
Stateful-TCP session table shipped in v0.2.88 (code-review-fixed in
v0.2.91) and has been stable, but it was the only session-table
surface in the app missing three patterns every other tab adopted
between v0.2.74 and v0.2.93. This release lifts those patterns over
verbatim so operators get the same UX whether they're looking at
L2 emulation, Devices, or Stateful TCP.

### What changed
- **Sort indicator survives the 3-second auto-refresh**
  (`widgets/stateful_tcp_tab.py:_render_sessions`). Click "Uptime"
  to sort by longest-running session and the indicator now stays
  put through every poll, instead of resetting to the default and
  snapping rows back to insertion order. Same `capture_sort_state` /
  `restore_sort_state` helper that landed on the Devices tab in
  v0.2.92.
- **"No sessions" placeholder when the table is empty**
  (`widgets/stateful_tcp_tab.py:__init__`). A blank table is
  ambiguous — is it empty? still loading? broken? The new
  `EmptyStateOverlay` centres a dimmed hint over the viewport
  pointing the operator at "Start session…" and Help → Capabilities
  → Stateful TCP. Auto-hides on the first session and reappears
  when the last drains. Same widget shipped for the Devices
  sub-tabs in v0.2.89.
- **Stop-selected shows a per-row results dialog**
  (`widgets/stateful_tcp_tab.py:_on_stop_selected` + new
  `_spawn_bulk_stop` / `_show_bulk_stop_results`). Pre-v0.2.94 the
  fan-out was fire-and-forget — operator only learned which SIDs
  stopped via the next 3-second poll, and when some failed there
  was no way to tell which without diffing the count chip. Now the
  same `MultiDeviceResultsDialog` that ships per-device results for
  BGP/OSPF/ISIS/VXLAN/DHCP (v0.2.93) collects ✅/❌ per SID and
  pops at the end of the fan-out. Defensive fallback to
  `QMessageBox.information` if the dialog can't construct, matching
  the v0.2.93 VXLAN-apply fallback pattern.

### What didn't change
- Per-row Stop button: untouched.
- Stop-all: still a single POST with empty body. No multi-row
  collection to surface — the server reports back via the next
  poll just fine.

### Tests
- **`tests/test_stateful_tcp_tab.py`** — 5 new tests, prefixed
  `test_v0_2_94_*` so the audit trail is preserved:
  - Empty-state overlay visibility flips as sessions arrive/drain.
  - Empty-state hint mentions the Start affordance (pin the
    operator-guidance copy so future refactors can't strip it).
  - Sort indicator survives a render cycle (regression for the
    pre-v0.2.94 reset behaviour).
  - Bulk-stop shows MultiDeviceResultsDialog with ✅/❌ prefixes,
    correct success/failure counts, and one row per SID.
  - Bulk-stop falls back to QMessageBox.information when the
    dialog constructor raises.

### Stateful TCP audit — PAIN tier closed
The remaining 6 POLISH items from the audit (right-click context
menu, Delete-key shortcut, live IP validators, payload preview
button, export CSV/JSON, optional last-error column) are deferred
to v0.2.95 / v0.2.96. None are user-visible breakage; daily users
get the biggest wins from this release.

### Test count
655 → 660 (+5).

## [0.2.93] - 2026-05-30

**Apply-result consistency across all 5 protocols** — closes the
last open Devices-tab audit POLISH item. ISIS apply, ISIS remove,
and VXLAN apply now show per-device results through the same
`MultiDeviceResultsDialog` that BGP and OSPF already use. Operators
get the same colour-coded ✅ / ❌ / ⚠️ / ℹ️ outcomes per device
regardless of which protocol they applied.

### What changed
- **`utils/devices_tab_isis.py:_apply_isis_to_devices`** — was
  silent on success, only popping a `QMessageBox.critical` on
  network errors. Now collects per-device results, shows the
  dialog at the end. Records skips with ℹ️ (missing device_id /
  config / server URL), successes with ✅, HTTP failures with ❌ +
  the server's error message (capped at 200 chars).
- **`utils/devices_tab_isis.py:_remove_isis_from_devices`** —
  same treatment. The dual-path nature (server removal + local
  removal) surfaces honestly: ✅ for full server+local success,
  ⚠️ for local-only when server removal failed or was skipped
  (typical for non-Docker devices or when the FRR container's
  already down).
- **`widgets/devices_tab.py:apply_vxlan_configurations`** —
  replaced the binary `QMessageBox.information` / `.warning`
  branches (which just listed device names comma-separated) with
  `MultiDeviceResultsDialog`. Each device gets its own
  colour-coded line, which scales when the operator selects more
  than a handful. Dialog-failure fallback retained so the legacy
  message still fires if the dialog itself can't be constructed.

### Why not the progress dialog too?
The audit also flagged BGP/OSPF using `QProgressDialog` while
ISIS/VXLAN/DHCP show nothing during the apply. Closing that gap
properly means moving the sync apply paths to async worker threads
(matching the `ApplyBGPWorker` pattern), which risks the same
QThread SIGABRT problems v0.2.20–v0.2.25 fixed. The
result-dialog consistency is the higher-value, lower-risk half;
the progress-dialog half is deferred to a focused follow-up where
the async-worker scaffolding can be done carefully.

### Tests
- **`tests/test_apply_result_consistency.py`** — new file, 10 tests:
  - 4 dialog-level Qt tests: results render as labelled rows,
    colour-coded by emoji prefix (green / red / orange / blue /
    purple), summary label rendered, empty results don't crash.
  - 3 parametrised source-grep guards confirming each apply path
    (`_apply_isis_to_devices`, `_remove_isis_from_devices`,
    `apply_vxlan_configurations`) imports
    `MultiDeviceResultsDialog`.
  - 2 tests confirming the new per-device-results list shape
    (`results = []`, ✅/❌ prefixes used).
  - 1 test confirming the VXLAN fallback-on-dialog-failure path
    still exists so a future dialog refactor can't silently strip
    operator feedback.

### Devices audit — fully closed
With v0.2.93 every PAIN + POLISH item from the v0.2.85 Devices-tab
audit has been shipped. Only deferred:
- **Async-worker progress dialogs** for the remaining 3 sync
  apply paths (see "Why not the progress dialog" above).
- **Sortable headers** for the OSPF / IS-IS / VXLAN / DHCP tables
  (feature add, not a bug; the v0.2.92 sort-state helper is
  ready when they get it).

### Test count
645 → 655 (+10).

## [0.2.92] - 2026-05-30

**Sort-state preservation across table rebuilds** — closes another
Devices-tab audit POLISH item. The BGP-neighbours table and the
main Devices table both follow the "disable sort → setRowCount →
repopulate → re-enable sort" pattern, but they were losing the
operator's chosen sort column + direction every time. Click
"Sort by State", hit Apply, the rebuild snapped rows back to
insertion order. Annoying enough that operators didn't bother
sorting at all.

### What changed
- **`utils/table_sort_state.py`** — new pure-function helper.
  `capture_sort_state(table)` snapshots the header's sort indicator
  (column + Qt.SortOrder); `restore_sort_state(table, state)`
  re-applies via `sortByColumn()`. Defensive against C++-side
  teardown (any access failure returns / consumes the `(-1, …)`
  sentinel and the rebuild keeps going).
- **`utils/devices_tab_bgp.py`** — BGP-neighbours rebuild captures
  the sort state before `setSortingEnabled(False)` and restores it
  after re-enabling.
- **`widgets/devices_tab.py`** — Devices-table rebuild does the
  same. Existing `_populating_devices_table` guard untouched.

The other 4 protocol tables (OSPF / IS-IS / VXLAN / DHCP) don't
have sorting enabled today; this release deliberately does NOT
turn it on — adding sortable headers is a feature add, not the
bug fix this release addresses. Same helper would slot in if
those tables get sortable later.

### Tests
- **`tests/test_table_sort_state.py`** — new file, 12 tests:
  - 4 parametrised round-trip tests covering column 0 + column 2
    in both ascending + descending order.
  - Capture returns Qt's default (column 0) when no header click
    has happened; -1 sentinel when the table lacks
    `horizontalHeader()` (defensive against rebuild-mid-flight).
  - Restore is a no-op on `None`, on the `(-1, …)` sentinel, and
    on a dead-C++ table (the "wrapped C/C++ object" failure mode).

### Audit remaining (after v0.2.92)
- Progress dialog consistency across protocols
- Error-message format consistency across protocols
- PIM Join/Prune (BLOCKER, full state-machine; roadmap)
- tx_worker C-side bundle (3 deferrals; needs binary rebuild)

### Test count
627 → 645 (+18 — 12 in this file + 6 stateful-TCP-tab tests now
green after the v0.2.91 fixup bundle).

## [0.2.91] - 2026-05-30

**Stateful TCP tab — fixup bundle from v0.2.88 code-review + GUI
smoke.** Six surgical fixes in `widgets/stateful_tcp_tab.py` covering
two HIGH-severity bugs (one of which blocked all remote-server use
of server-side TLS), three MED bugs, and one UX bug surfaced by
running the dialog in the offscreen Qt smoke. Six regression tests
added to keep them away.

### What changed

#### Finding #1 — Cert/key path validation removed (HIGH)
- The dialog used to call `os.path.isfile()` on cert/key paths before
  POSTing to `/api/stateful_tcp/start`. Those paths are read by the
  netgen-server, not by the GUI host — anyone running the client on
  a workstation against a remote server had every valid server-side
  path rejected with a false-positive "file not found" error.
- Fix: dropped the check entirely. The server is the authoritative
  validator; its non-200 response surfaces via the existing
  Start-failed dialog path.

#### Finding #2 — Stale Stop button on running→stopped row (HIGH)
- `setItem(row, COL_ACTION, QTableWidgetItem(""))` does NOT clear a
  previously-installed `setCellWidget` Stop button at the same cell
  — cellWidget always wins over setItem in Qt. A session that
  flipped running→stopped between polls would keep its now-stale
  Stop button visible and clickable, POSTing /stop for a dead
  session_id on click.
- Fix: `self._table.removeCellWidget(row, self.COL_ACTION)` before
  the `setItem` call in the stopped branch.

#### Findings #3 + #6 — TLS-group row visibility (MED-HIGH + smoke)
- `_on_role_changed` was hiding only the inner `QLineEdit`s for the
  cert/key fields, leaving the wrapping `QWidget`s — which also
  contained the "Browse…" `QPushButton`s — fully visible. Result:
  two orphan Browse buttons floated in the client-TLS view with no
  input next to them, visually suggesting required cert files for
  client mode.
- GUI smoke surfaced a sister bug (#6): `QFormLayout.addRow(<string>,
  field)` builds the label internally with no handle to hide it. In
  server-TLS view the "SNI hostname:" label stayed visible with no
  field next to it.
- Fix: capture the cert/key Browse-wrap `QWidget`s as
  `_tls_cert_wrap` / `_tls_key_wrap` and toggle their visibility
  instead of the inner QLineEdits; capture explicit `QLabel`s
  (`_tls_sni_label`, `_tls_verify_label`) for every previously
  string-keyed row. `_on_role_changed` now toggles label + field as
  a pair for every row.

#### Finding #4 — Synchronous stop POSTs blocking GUI (MED)
- All three stop paths called `requests.post(..., timeout=5)` (or
  10 for Stop all) on the GUI thread. A slow / hung server froze
  the UI for up to 5 s per Stop click, or 5 × N for Stop selected.
- Fix: new `_JsonPostWorker` QThread (sibling of `_JsonFetchWorker`)
  emits `done(http_code, msg)` for logging. New helper
  `_spawn_stop_post(body, timeout_s, tag)` fires-and-forgets via the
  worker; the next refresh poll surfaces the result. All three stop
  paths now use it. UI never blocks.

#### Finding #5 — Stop selected ignored filter-hidden rows (MED)
- `selectionModel().selectedIndexes()` returns every selected row
  regardless of `setRowHidden` state. An operator could select 5
  rows, type a filter substring that hid 3 of them, click Stop
  selected, and silently stop sessions they could no longer see.
- Fix: filter selected rows on `self._table.isRowHidden(row)` before
  walking them; updated the empty-selection message to say "visible
  session rows".

### Tests
- **`tests/test_stateful_tcp_tab.py`** — 6 new regression tests, one
  per finding, each named `test_v0_2_91_finding_N_…` so the audit
  trail is preserved in the test report:
  - `_finding_1_server_tls_does_not_validate_path_on_client_fs` —
    asserts a non-existent path is accepted into the payload and no
    "file not found" warning fires.
  - `_finding_2_stale_stop_button_evicted_on_running_to_stopped` —
    renders a running session, flips to stopped, asserts
    `cellWidget(...) is None`.
  - `_finding_3_browse_buttons_hidden_in_client_tls_mode` —
    constructs the dialog, checks TLS, asserts no visible Browse
    button in the TLS group while role=Client.
  - `_finding_6_sni_label_hidden_in_server_tls_mode` — asserts
    `_tls_sni_label.isVisible() is False` in server mode (would
    have caught the smoke-surfaced bug).
  - `_finding_4_stop_posts_run_off_gui_thread` — monkeypatches
    `_JsonPostWorker.__init__` to capture every construction and
    asserts a worker is spawned per Stop click (not a direct
    `requests.post`).
  - `_finding_5_stop_selected_skips_filter_hidden_rows` — selects
    3 rows, hides one via `setRowHidden(True)`, asserts the
    hidden row's SID is NOT in the captured stop POSTs.
- Two pre-existing tests updated (`test_tab_per_row_stop_posts_…`,
  `test_tab_stop_all_posts_empty_body_…`) to collapse the new async
  hop via a `_patch_async_post_to_sync` helper that monkeypatches
  `_JsonPostWorker.start` to call `run()` inline. The capture
  pattern via `mod.requests.post` stays identical.
- Full suite: **633 tests passing** (was 613 at v0.2.88 head).

### Files changed
- Edit: `widgets/stateful_tcp_tab.py` (~+86 / -36),
  `tests/test_stateful_tcp_tab.py` (~+170 / -25).


## [0.2.90] - 2026-05-30

**Help-guide refresh for v0.2.80 → v0.2.89** — third doc-catch-up
in the series (v0.2.72, v0.2.79, now this). What's New had drifted
9 releases behind the actual code; this release closes the gap and
extends the pinning tests through v0.2.89 so the next stale period
fails CI immediately.

### What's New (`_FEATURE_GUIDE_HTML`) additions

#### L2 Emulation section — 4 new entries
- `0.2.81` Submit-time MAC + IP validators (LACP / LLDP / VRRP /
  IGMP / PIM / BFD). Bundled: VRRP v2 + IPv6 mismatch rejected
  up-front; PIM gen_id bounds-checked; IGMP group tooltip.
- `0.2.82` IGMPv1 (RFC 1112) support — legacy multicast-router
  tests; type-code default 0x12 (Report), override to 0x11 (Query)
  → IP dst = 224.0.0.1.
- `0.2.83` VRRPv2 authentication (RFC 3768 §5.3.6) — Auth type
  combo + 8-ASCII-byte password, fields disable on v3 with RFC
  5798 §5.1 tooltip.
- `0.2.84` Sessions-table polish + Frame preview — Last Error 120
  → 200 chars, filter QLineEdit, per-row Stop button, Preview
  modal with scapy summary + tcpdump-style hex.

#### Devices tab additions — NEW top-level section
- `0.2.85` Right-click menu + Delete-key shortcut.
- `0.2.85` DHCP apply now refreshes preflight (closes the 5-protocol
  consistency loop).
- `0.2.86` ISIS NET-ID validation (RFC 1195 §3.1) — variable-length
  area IDs, NSEL=00 enforcement, both inline-edit + Add Device
  dialog.
- `0.2.87` OSPF area-id validation + normalisation — `1` → `0.0.0.1`,
  matches what FRR puts on the wire.
- `0.2.89` Empty-state placeholders on every protocol sub-tab.

#### Stateful TCP tab — NEW top-level section
- `0.2.88` First-class GUI tab — client + server roles, raw + http
  protocols, TLS both directions, VRF passthrough, loopback warning,
  sessions table with per-row Stop, graceful 404 degrade.

#### Reliability fixes
- `0.2.88` Stateful-TCP suite-flake fix — five tests throttled to
  `interval_s=0.02` to stop emptying the macOS ephemeral-port pool
  inside a single test. 20 consecutive full-suite runs green after
  the fix.

#### Stale test count
`436 tests` → `622 tests`. (Suite is at 622 as of the v0.2.89 ship;
v0.2.90 adds 5 more pin tests.)

### Capabilities (`_CAPABILITIES_GUIDE_HTML`) updates
- §2 L2 / Multicast emulator table — VRRP row notes the 0.2.83 v2
  auth TLVs (and explains why v3 has no auth fields); IGMP row
  promoted from "v2 + v3" to "v1 + v2 + v3" with the 0.2.82
  reference.
- §7 Preflight "Findings surfaces" — DHCP added to the
  auto-refresh-after-Apply list (closing the 5-protocol loop).
  New sub-block "Catch-bad-config-early validators" names both
  helpers (`utils/isis_net.py`, `utils/ospf_area.py`) and explains
  the normalisation behaviour.
- §11 Stateful TCP (capability matrix) — already added in v0.2.88;
  no change needed.

### Tests
- **`tests/test_help_dialogs.py`** — 5 new tests + 9 new version
  pins:
  - `test_feature_guide_references_recent_versions` extended through
    `0.2.89`.
  - `test_feature_guide_documents_l2_validation_burst` pins IGMPv1
    / VRRPv2 auth / Frame preview / MAC+IP validators strings.
  - `test_feature_guide_documents_devices_tab_additions` pins the
    new section + each Devices-tab feature label.
  - `test_feature_guide_documents_stateful_tcp_tab` pins the
    Stateful TCP section + signature labels.
  - `test_capabilities_guide_lists_igmpv1_and_vrrpv2_auth` pins the
    §2 updates.
  - `test_capabilities_guide_has_validators_subsection` pins the §7
    validator sub-block.

### Test count
622 → 627 (+5).

## [0.2.89] - 2026-05-30

**Empty-state placeholders on protocol sub-tabs** — closes another
Devices-tab audit POLISH item. The 5 protocol sub-tabs (BGP / OSPF
/ IS-IS / VXLAN / DHCP) used to render as blank rectangles when
nothing was configured yet; operators new to the app couldn't tell
"empty by design" from "broken connection" or "still loading".

### What changed
- **`widgets/empty_state_overlay.py`** — new reusable widget.
  `EmptyStateOverlay(table, message)` overlays a centred dimmed
  label on the table's viewport when `rowCount() == 0`. Show/hide
  is driven by the model's `rowsInserted` / `rowsRemoved` /
  `modelReset` / `layoutChanged` signals so it disappears the
  instant the first row arrives. Re-centres on viewport resize
  via an installed event filter. Click-transparent so right-click
  context menus still reach the table underneath. Defensive
  against Qt teardown order (signals firing after the QLabel is
  C++-deleted are no-oped).
- **`utils/devices_tab_{bgp,ospf,isis,vxlan,dhcp}.py`** — each
  sub-tab construction wires an overlay with a per-protocol
  message naming the action that creates the first row (e.g.
  "Add one with the Add button below, or configure BGP on a device
  via the main Devices table"). All wrapped in try/except so a
  construction failure can't block the sub-tab from rendering.

### Tests
- **`tests/test_empty_state_overlay.py`** — new file, 9 tests:
  - Overlay visible on empty table at construction (requires
    `parent.show()` for `isVisible()` to return True under
    offscreen Qt).
  - Hidden when rows present at construction (initial `_refresh`
    runs in the constructor).
  - Hides on first `insertRow`; reappears on last `removeRow`.
  - `setRowCount(0)` + explicit `refresh()` re-shows (programmatic
    setRowCount doesn't always fire the rowsRemoved signal).
  - `set_message()` swaps label text in place.
  - `WA_TransparentForMouseEvents` set so clicks pass through to
    the table.
  - Reparents to `table.viewport()` (not the table itself) so it
    sits inside the data area.
  - Viewport resize re-centres the label via the event filter.

### Defensive teardown
The first test draft hit a cross-test segfault — model signals
fired after the QLabel was C++-deleted. Fixed two ways:
1. The overlay's `_refresh` catches `RuntimeError` from a deleted
   QLabel and returns silently.
2. The test fixture uses a `yield`-based teardown that closes +
   deleteLater()s the parent QWidget so children clean up before
   the next test's QApplication state.

### Audit remaining (after v0.2.89)
- Progress dialog consistency across protocols
- Error-message format consistency across protocols
- Sort-state preservation across table rebuild

### Test count
613 → 622 (+9).

## [0.2.88] - 2026-05-30

**Stateful-TCP GUI tab + suite-flake fix** — gives the stateful-TCP
feature (previously API/CLI-only) a first-class tab next to L2
Emulation, and fixes a kernel-resource flake that intermittently
killed five tests across the stateful-TCP / DNS / SIP suites on
macOS.

### What changed

#### Test-suite flake fix — TIME_WAIT / EADDRNOTAVAIL
- Five tests (`test_echo_round_trip_counts_bytes_both_ways`,
  `test_http_protocol_status_2xx_counter_moves`,
  `test_dns_nxdomain_counters_increment`,
  `test_dns_noerror_when_server_configured`,
  `test_sip_register_2xx_counters_increment`) were running the
  stateful-TCP client with the default `interval_s=0`. A single
  sender thread hammers ~5000 connect()s/sec on loopback; every
  completed handshake leaves a TIME_WAIT for 30–60 s; the macOS
  ephemeral-port pool (~16 k ports) empties out inside the same
  test → `OSError [Errno 49] Can't assign requested address`.
  Subsequent stateful-TCP tests inherited the dry port pool and
  flaked sporadically — including `test_vrf_bind_no_op_on_non_linux`,
  which was the user-visible symptom.
- **Fix**: added `interval_s=0.02` to all five offending tests
  (`tests/test_stateful_tcp.py`, `tests/test_dns_over_tcp.py`,
  `tests/test_sip_over_tcp.py`). Brings each to ~50 conns/sec —
  well under the TIME_WAIT recycle window — while still exercising
  the same assertions. Matches the throttle pattern already used by
  the TLS + VRF tests in the same file.
- 20 consecutive full-suite runs green after the fix.

#### New Stateful TCP tab (`widgets/stateful_tcp_tab.py`, 1233 lines)
- Session-based GUI tab modelled on `widgets/l2_emulation_tab.py` —
  same action-bar / table / poll-worker shape so the two tabs read
  as siblings.
- **Roles**: client + server in one dialog, switched via
  `QButtonGroup` driving a `QStackedWidget`.
- **Protocols**: `raw` + `http` in v1 (the API-side `dns` / `sip`
  are deferred to v2 alongside matching `netgen-cli tcp` flag adds).
- **TLS**: both directions. Client gets `verify` + SNI inputs;
  server gets cert + key file pickers with file-exists validation.
- **VRF**: passed through unchanged — the API-side helper degrades
  gracefully on macOS / non-root Linux (last_error carries the
  reason, traffic still flows via the default routing table).
- **Loopback warning** — inline amber callout under the Interval
  field when `dst_ip` parses as loopback and `interval_s < 0.005`.
  GUI-side guard against the same EADDRNOTAVAIL trap the test-flake
  fix above addresses; non-blocking (operator can still hit Start).
- **Session table** — 10 columns (Status / Role / Protocol+TLS /
  Target / Conns / Bytes TX / Bytes RX / Avg RTT / Uptime / Session
  ID) + per-row Stop button. 3 s auto-refresh. Hover the Status cell
  for the full counter dump (per-protocol bins + last_error +
  retransmits + kernel RTT) — keeps the column count glanceable
  while preserving every counter the worker tracks.
- **Graceful degrade on 404** — same "unsupported mode" pattern as
  L2 tab: slowed poll, amber chip, intercepted Start, auto-recovery
  when the endpoint returns.

#### Wired into the main window (`traffic_client/main.py`, +20 lines)
- Lazy-guarded import (same pattern as `L2EmulationTab`) so an
  import-time exception doesn't prevent the client from starting.
- New "Stateful TCP" tab inserted right after "L2 Emulation".
- `cleanup_threads()` hook in the close path so the 3 s poll worker
  is drained before the QApplication tears the event loop down.

#### Help → Supported Features dialog — new §11
- **`widgets/stream_dialog.py`** — appended `<h2>11. Stateful TCP
  (real-socket traffic generator)</h2>` to `_CAPABILITIES_GUIDE_HTML`
  with nine subsections:
  - 11a. What's supported in the GUI (capability matrix)
  - 11b. Scale & limits (knob ranges, server-side process limits,
    observed throughput envelope, ephemeral-port ceiling math for
    macOS + Linux)
  - 11c–11f. Four end-to-end workflows (middlebox/proxy soak,
    WAF/TLS termination, VRF pinning, loopback dev smoke incl. the
    EADDRNOTAVAIL trap).
  - 11g. Reading the session table.
  - 11h. Stop operations (per-row / selected / all / implicit).
  - 11i. When something looks wrong — 4-step diagnostic ladder.

#### API_GUIDE.md / README.md — VRF + loopback caveat documented
- **`API_GUIDE.md`** §8 — added three new subsections after the
  stats response: VRF binding semantics (3-row platform matrix:
  Linux-root / Linux-non-root / macOS-Windows with exact last_error
  strings), Loopback testing caveat (the EADDRNOTAVAIL trap with
  both macOS errno 49 and Linux errno 99 spelled out), CLI coverage
  gap (note that `netgen-cli tcp` exposes `--protocol raw|http`
  only; DNS / SIP via API directly).
- **`README.md`** § Stateful TCP — surfaced DNS-over-TCP and
  SIP-over-TCP rows in the "When to use which" table (flagged
  `API only`); expanded the VRF paragraph; added a
  `### Loopback caveat` section with the EADDRNOTAVAIL guidance;
  refreshed the stale "36 pytest cases" intro line (the suite is
  larger now). Counter list gained the full `dns_*` + `sip_*` bins.

### Tests
- **`tests/test_stateful_tcp_tab.py`** — new file, 34 tests:
  - Pure validators: `_validate_ip`, `_validate_port`,
    `_is_loopback` (4 tests).
  - Dialog visibility wiring: default role, role-toggle stack swap,
    TLS-checkbox group visibility, role+TLS interaction (verify+SNI
    vs cert+key), protocol-combo greys response_bytes on raw,
    loopback warning fires on `127.0.0.1` + `interval=0`, silent
    otherwise (7 tests).
  - Dialog payload assembly: client default shape, optional
    `src_ip` + `vrf` carried, TLS-on carries `verify` + SNI, server
    default shape, server-HTTP carries `response_bytes`, invalid
    `dst_ip` rejected, server-TLS without cert/key rejected
    (7 tests).
  - Tab rendering: empty-table construct, running client/server
    rendered correctly (target column built from role-specific
    config), `HTTP+TLS` protocol badge, status tooltip carries
    HTTP / DNS / SIP protocol bins, stopped session has no Stop
    button, count chip math (8 tests).
  - Tab failure paths: 404 enters unsupported mode, recovery on
    next successful poll, 401/403 surfaces in info label without
    entering unsupported mode (3 tests).
  - Tab stop paths: per-row Stop POSTs correct session_id (guards
    against closure-in-loop trap), Stop-all POSTs empty body after
    confirm, Stop-all aborts when user picks No (3 tests).
  - Lifecycle: `cleanup_threads()` stops the poll timer (1 test).
- Full suite: **613 tests passing**, no flakes across 20+ runs.

### Files changed
- New: `widgets/stateful_tcp_tab.py` (1233 lines),
  `tests/test_stateful_tcp_tab.py` (554 lines).
- Edit: `traffic_client/main.py` (+20), `widgets/stream_dialog.py`
  (+382, §11 + §11b), `API_GUIDE.md` (+27),
  `README.md` (+39), `tests/test_stateful_tcp.py` (+6),
  `tests/test_dns_over_tcp.py` (+2),
  `tests/test_sip_over_tcp.py` (+1).


## [0.2.87] - 2026-05-30

**OSPF area-id refactor + normalisation (RFC 2328 §6)** — closes
the second-to-last validation deferral from the v0.2.85 Devices-tab
audit. Lifts the inline decimal-or-dotted check into a shared
helper and adds normalisation so the stored config matches what FRR
puts on the wire.

### What changed

#### New helper
- **`utils/ospf_area.py`** — new module.
  `validate_ospf_area_id(value)` returns `(ok, normalised_dotted,
  error)`. Accepts plain integer form (0–4294967295) or dotted-
  decimal (each octet 0–255); normalises to dotted-decimal IPv4
  shape (the form FRR / Quagga / Cisco IOS all put on the wire).
  Companion `normalise_ospf_area_id(value)` returns just the
  normalised string for call sites that don't need the error text.

#### Normalisation matters
The Add Device dialog used to store whatever the operator typed —
`1` for area 1 stayed as `1`, but FRR's `show ip ospf` later
reported it as `0.0.0.1`. Subsequent inline-edits saw the mismatch
as a change and triggered apply storms. Normalising at submit
time + at inline-edit time keeps the stored config and the live
output in lockstep.

#### Wired into entry points
- **`widgets/add_device_dialog.py`** Add Device → OSPF Area ID
  field (line 1775). Validates at submit; bails on hard-invalid
  input with a clear modal explaining BOTH accepted forms; stores
  the dotted-decimal normalised value.
- **`utils/devices_tab_ospf.py`** inline-edit handler (line 1157).
  Replaced the hand-rolled try/except nest with the shared
  validator + normaliser. When the operator types `1`, the cell
  is updated to `0.0.0.1` in place so the display matches what
  goes into the config dict.

### Tests
- **`tests/test_ospf_area.py`** — new file, 37 tests:
  - 12 parametrised happy-path tests covering integer form (0,
    1, 100, 256, 65535, 4294967295) and dotted form (0.0.0.0,
    0.0.0.1, 10.0.0.1, 255.255.255.255).
  - Whitespace stripping; leading-zero canonicalisation
    (`001.002.003.004` → `1.2.3.4`).
  - 6 parametrised `_int_to_dotted` round-trip tests covering
    every byte-shift boundary.
  - Rejection tests: empty, negative, > 2³², garbage no-dots,
    7 parametrised malformed-dotted variants (too few/many
    parts, octet > 255, negative octet, empty octet, non-numeric
    octet).
  - Error-message quality: names which octet is bad, names actual
    part count, no Python internals in user-facing strings.
  - Backbone area accepted in both forms + normalised to same
    canonical value.

### Audit remaining (after v0.2.87)
- Progress dialog consistency across protocols
- Empty-state placeholders in protocol sub-tabs
- Error-message format consistency
- Sort-state preservation across table rebuild

### Test count
576 → 613 (+37).

## [0.2.86] - 2026-05-30

**ISIS NET-ID validation (RFC 1195 §3.1)** — closes one of the
PAIN items the v0.2.85 Devices-tab audit deferred. Lifts a hand-
rolled hardcoded-6-part check from inline in
`utils/devices_tab_isis.py` into a shared pure-function helper
that supports the variable-length area IDs the spec actually
allows.

### What changed

#### New helper
- **`utils/isis_net.py`** — new module. `validate_isis_net(net_id,
  allow_short_area=False)` returns None on valid or a short
  human-readable reason. The check is bytewise: strip dots
  (operators paste in either Cisco's 4-char-group form or
  Juniper's nibble form; both work), require hex-only + even
  hex-char count, total byte length in [8, 20], last byte = `00`
  (NSEL — IS-IS requires zero per RFC 1195 §3.1; a non-zero NSEL
  indicates an OSI NSAP for a transport service, not the routing
  protocol).
- `allow_short_area=True` accepts the dialog's AFI.Area shortcut
  (e.g. `49.0001`) which gets padded to a full NET on submit;
  anything 2–14 bytes passes in that mode.
- Companion `is_short_area_form(net_id)` helper for callers that
  want to classify input shape without validating it.

#### Wired into entry points
- **`utils/devices_tab_isis.py`** inline-edit handler — replaced
  the hand-rolled 6-part check (lines 1172-1244) with the shared
  helper. The old check rejected legitimate variable-length area
  IDs and didn't enforce NSEL=00; partial-input handling
  preserved via the "too short" / "odd hex-character count" error
  subtypes (those don't pop the modal so the operator can keep
  typing). Definitely-invalid input (non-hex, NSEL != 00, > 20
  bytes…) shows a clear modal naming the error and reverts the
  cell.
- **`widgets/add_device_dialog.py`** Add Device → ISIS Area ID
  field — validates at submit time with `allow_short_area=True`.
  Bails out of submit on hard-invalid input so the operator sees
  a clear reason instead of FRR rejecting it 5 seconds later
  inside the container.

### Tests
- **`tests/test_isis_net.py`** — new file, 33 tests:
  - 6 parametrised "valid full NETs" (Cisco shape, longer-area
    shape, mixed-case hex, lowercase hex, AFI 47 GOSIP, longer
    area+sysid).
  - 4 parametrised "short area form" tests (with/without flag).
  - 3 empty-input tests + 1 dots-only.
  - Non-hex char rejected WITH position; odd hex count;
    too-short / too-long bounds.
  - 5 parametrised "non-zero NSEL rejected" tests.
  - 7 tests for the `is_short_area_form` classifier.
  - 1 regression-style test that error messages don't leak Python
    internals (no `traceback` / `nonetype` / `attribute` chatter
    in the strings operators read).

### Audit remaining
- OSPF area-id parsing refactor (existing IPv4-vs-int fallback nest)
- Progress dialog consistency across protocols
- Empty-state placeholders in protocol sub-tabs
- Error-message format consistency
- Sort-state preservation across table rebuild

### Test count
509 → 576 (+67; the +33 from `test_isis_net.py` plus pre-existing
tests that the new file enabled via shared fixtures).

## [0.2.85] - 2026-05-30

**Devices tab audit fixes (round 1)** — first wave from a holistic
audit of the Devices tab + per-protocol helpers (BGP/OSPF/ISIS/VXLAN
/DHCP). The audit found 17 items; this release closes the 4 most
impactful + drops 1 audit miss (verified the alleged gap doesn't
exist). Remaining 12 items are mostly POLISH and validation refactors
that warrant their own focused passes.

### Bug fixes
- **Duplicate `apply_bgp_configurations` removed.** widgets/devices_tab.py
  had TWO defs of `apply_bgp_configurations` + matching start/stop
  wrappers — Python last-def-wins meant the v0.2.74 versions (with
  the `kick_refresh` hook) won, but the dead earlier defs (lines
  3195-3203) were a refactor trap. Same for `start_bgp_protocol` and
  `stop_bgp_protocol`. Cleaned up; pinned to "exactly one def" by
  tests.
- **DHCP apply now kicks the preflight bar.** Every other protocol's
  apply path calls `kick_refresh` (v0.2.71/74); DHCP was the lone
  outlier. DUPLICATE_IPV4 finding state can flip when a DHCP-assigned
  address collides with a static one, so the bar should repaint.
  Pinned by a parametrised `test_every_apply_path_kicks_preflight`
  that walks all 5 protocols.

### Affordances
- **Delete-key shortcut for selected device(s)** — standard
  table-keyboard expectation; widgets/devices_tab.py was the only
  big table without it. Scoped to `self.devices_table` with
  `Qt.WidgetShortcut` context so a Delete key during inline editing
  doesn't accidentally drop the row.
- **Right-click context menu on the devices table.** Apply selected
  / Copy / Paste / Delete — same 4 actions the toolbar exposes.
  Handler selects the row under the cursor first (so operators
  don't have to left-then-right-click), and Paste is greyed out
  when the clipboard is empty.

### Audit miss
- **VXLAN VNI range validation** was flagged as missing but is
  actually in place — `QIntValidator(1, 16777215)` on both
  `widgets/add_vxlan_dialog.py:61` and `widgets/add_device_dialog.py:974`,
  plus a submit-time int() check. The VXLAN table itself is
  `NoEditTriggers` so no inline-edit bypass either. Noted in the
  task description and skipped.

### Deferred from the audit
- **ISIS NET-ID format validation** — `mm.nnnn…nnnn` regex is
  fiddly and easy to get wrong; own focused release.
- **OSPF area-id parsing refactor** — existing IPv4-vs-int fallback
  nest is messy; surgical refactor needed.
- **Progress dialog consistency across protocols** — different
  protocols use different patterns.
- **Empty-state placeholders** in protocol sub-tabs.
- **Error-message format consistency** (BGP/OSPF/ISIS/VXLAN each
  use a different shape).
- **Sort-state preservation across rebuild.**

### Tests
- **`tests/test_devices_tab_audit.py`** — new file, 13 tests:
  - 2 tests pinning the duplicate-def cleanup (apply / start / stop
    BGP each exactly-once).
  - 1 test confirming `apply_dhcp_pools` calls `kick_refresh`.
  - 1 test confirming Delete-key shortcut wiring (correct widget +
    context + slot).
  - 4 tests covering the right-click menu (custom policy +
    handler exists; menu offers Apply/Copy/Paste/Delete; selects
    row under cursor; Paste disabled without clipboard).
  - 5 parametrised tests asserting `kick_refresh` lives in EVERY
    protocol's apply method (BGP/OSPF/ISIS/VXLAN/DHCP). Closes
    the "did we wire it everywhere?" question once and for all.

### Test count
496 → 509 (+13).

## [0.2.84] - 2026-05-30

**L2 emulation POLISH bundle** — closes the 4 remaining POLISH items
from the v0.2.81 L2 audit. The audit is now fully resolved
(BLOCKERs in v0.2.82 + v0.2.83, PAIN in v0.2.81, POLISH here)
except the explicitly-deferred PIM Join/Prune state machine.

### What changed

#### Last Error column 120 → 200 chars (audit POLISH #12)
- `widgets/l2_emulation_tab.py` — `err[:120]` → `err[:200]`. Scapy
  tracebacks ("ValueError: invalid literal for int() with base 16: …")
  used to get chopped mid-message; 200 is enough for the common
  cases without making the column unwieldy. Full text still in
  the tooltip.

#### Filterable sessions table (audit POLISH #14)
- New `Filter:` QLineEdit in the action bar — substring-matches on
  protocol / iface / session_id (case-insensitive). Empty filter
  shows everything; non-empty hides rows via `setRowHidden`. Survives
  the 3 s poll because `_apply_session_filter` runs at the end of
  every `_render_sessions`.
- **Sorting** considered but **intentionally skipped** — Qt's
  `setSortingEnabled` reorders items on header click but NOT the
  cellWidget-based per-row Stop buttons (next item), so a click
  after sort would fire the wrong session's Stop. Pinned by a
  `test_sessions_table_sorting_intentionally_off` test so a future
  refactor that re-enables sort without also fixing the cellWidget
  association fails CI loudly.

#### Per-row Stop button (audit POLISH #13)
- New COL_ACTION column with a tiny red `Stop` QPushButton on every
  running row. Single click → `POST /api/l2/stop` with that
  session_id, then a 150 ms `singleShot` refresh so the row flips
  from running → stopped without waiting for the next 3 s poll.
- Stopped rows get a placeholder QTableWidgetItem (so the column
  still reads cleanly in a future sort-aware rebuild). Default-arg
  lambda captures each row's session_id (same closure-in-loop trap
  the EVPN Inject dialog hit in v0.2.63).

#### Frame preview (audit POLISH #11)
- `utils/l2_protocols.py` gains `build_preview_frame(protocol, body)`
  — pure synchronous function that builds the first frame each
  factory would emit, with no threading and no session registration.
  Returns a scapy Packet (or None if protocol unrecognised). Mirrors
  every per-protocol RFC mapping the live factories use:
  LACP / LLDP TLV stack / VRRPv2 + v3 / IGMPv1+v2+v3 with the right
  L3 destinations / PIM Hello with options / BFD with the packed
  24-byte payload.
- `widgets/l2_emulation_tab.py` Start dialog gains a `Preview
  frame…` button. Calls `_on_preview` which flips a `_preview_mode`
  flag, runs `_on_accept` (so the same MAC/IP validators fire),
  reads the resolved body, hands it to `build_preview_frame`, and
  shows scapy's `summary()` + tcpdump-style hex dump in a modal —
  WITHOUT closing the Start dialog.

### Tests
- **`tests/test_l2_preview_and_polish.py`** — new file, 16 tests:
  - 6 parametrised `build_preview_frame` smoke tests (one per
    protocol; each yields a non-empty frame from an empty body).
  - VRRPv2 simple-password preview round-trips through auth1/2
    with the right NUL padding (the v0.2.83 wiring works in the
    preview path too).
  - IGMPv1 query preview targets 224.0.0.1, not the group (v0.2.82
    wiring).
  - QinQ preview produces a frame with outer ethertype 0x88a8.
  - Sessions table COL_ACTION column exists; filter hides
    non-matching rows; per-row Stop button on running rows only
    (stopped row has placeholder item); closure-capture regression
    (3 rows fire their own session_ids).
  - Last Error cell capped at 200; tooltip carries the full text.

### v0.2.81 L2 audit — final tally
- ✅ All 5 PAIN items shipped in v0.2.81.
- ✅ BLOCKER #1 IGMPv1 shipped in v0.2.82.
- ✅ BLOCKER #2 VRRPv2 auth shipped in v0.2.83.
- ⏸️ BLOCKER #3 PIM Join/Prune — explicitly deferred (full PIM-SM
  state machine, much bigger lift than a single release).
- ✅ All 4 POLISH items shipped here.

### Test count
480 → 496 (+16).

## [0.2.83] - 2026-05-30

**VRRPv2 authentication (RFC 3768 §5.3.6)** — closes the second of
three BLOCKER items from the v0.2.81 L2 audit. Operators testing
peer behaviour around auth-type or password mismatches can now
generate the frames from the GUI.

### Why not VRRPv3 too?
The audit asked for "VRRPv3 auth extension TLV (RFC 5798)" but on
closer reading RFC 5798 §5.1 **removed authentication from the
protocol entirely** — no auth field exists in v3 at all (the spec
defers to IPsec at the IP layer). So this release wires v2 auth
properly and surfaces v3's auth-less design correctly in the GUI
(fields disabled with a tooltip naming the RFC).

### What changed
- **`utils/l2_protocols.py`** — `start_vrrp()` gains two kwargs:
  - `auth_type: int = 0` — 0=None, 1=Simple Text Password, 2=IPAH.
  - `auth_data: str = ""` — up to 8 ASCII bytes packed into the
    `auth1` + `auth2` 4-byte fields (NUL-padded if shorter,
    truncated if longer). Only honoured for version=2; ignored
    with a debug log when version=3.
  - When auth_type=2 (IPAH) the type byte is set but the AH
    payload itself stays the operator's responsibility (IPsec
    handles AH end-to-end; VRRP just carries the type indication).
- **`widgets/l2_emulation_tab.py`** — VRRP panel gains:
  - `Auth type:` QComboBox with the three RFC codes.
  - `Auth data:` QLineEdit with `setMaxLength(8)` so the operator
    can't enter more than the RFC allows.
  - Both fields **disabled** when Version=v3 via a
    `currentIndexChanged` signal on the version combo, with a
    tooltip explaining RFC 5798 §5.1's removal of authentication.
- **`server/l2_routes.py`** — `_PROTOCOL_FACTORIES["vrrp"]`
  kwarg-allow-list gains `auth_type` + `auth_data`.

### Tests
- **`tests/test_l2_protocols.py`** — 3 new packet-shape tests:
  - `test_vrrpv2_auth_type_simple_packs_password_into_auth1_auth2`
    confirms "secret" round-trips through auth1+auth2 with correct
    NUL padding.
  - `test_vrrpv2_auth_type_none_zeroes_auth_fields`.
  - `test_vrrpv2_auth_type_ipah_sets_type_byte_only`.
- **`tests/test_l2_dialog_validation.py`** — 4 new dialog tests:
  - `test_vrrp_auth_fields_present_and_enabled_for_v2` (all 3
    codes available; enabled for v2).
  - `test_vrrp_auth_fields_disabled_for_v3` (disabled + tooltip
    cites RFC 5798).
  - `test_vrrp_auth_data_truncated_to_8_chars_via_maxlength`.
  - `test_vrrp_v2_payload_carries_auth_fields` end-to-end through
    `_on_accept`.

### Remaining from the v0.2.81 audit
Only **PIM Join/Prune** (BLOCKER, marked roadmap) and the 4 POLISH
items (frame preview, per-row Stop, sortable sessions, Last Error
truncation) remain. PIM Join/Prune is the full PIM-SM state machine
— a bigger lift than a single release.

### Test count
473 → 480 (+7).

## [0.2.82] - 2026-05-30

**IGMPv1 (RFC 1112) support** — closes the first of the three
BLOCKER items the v0.2.81 L2 audit flagged. Operators testing
legacy IGMPv1 query/report behaviour (older multicast routers,
IGMP-snooping fallback on switches that still accept v1) can now
generate the frames from the GUI without dropping to scapy.

### What changed
- **`utils/l2_protocols.py`** — `start_igmp()` gains a `version == 1`
  branch:
  - Type code default 0x12 (Membership Report) → IP dst = group;
    matches RFC 1112 §4 "Host-to-Router" report semantics.
  - Override `type_code=0x11` → IP dst = 224.0.0.1 (ALL-SYSTEMS),
    L2 dst auto-mapped to 01:00:5e:00:00:01 — that's the General
    Query a router would emit.
  - `mrcode` forced to 0 (v1 spec reserves the byte; v2 reuses it
    as max-resp-time, which v1 readers would misinterpret).
  - Existing v2 (0x16 Report / 0x17 Leave / 0x11 Query) and v3
    (0x22 Report) code paths untouched.
- **`widgets/l2_emulation_tab.py`** — IGMP version combo gains
  `v1 (RFC 1112)` as the first entry. Default explicitly pinned
  at v2 (`setCurrentIndex(1)`) so the established default doesn't
  flip from under operators. Type-code placeholder updated to
  mention the v1 codes.

### Tests
- **`tests/test_l2_protocols.py`** — 2 new tests:
  - `test_igmpv1_membership_report_target_is_group` pins type=0x12,
    IP dst=group, TTL=1, mrcode=0.
  - `test_igmpv1_membership_query_target_is_all_systems` pins
    type=0x11, IP dst=224.0.0.1, L2 dst=01:00:5e:00:00:01.
- **`tests/test_l2_dialog_validation.py`** — 3 new tests:
  - `test_igmp_version_combo_offers_v1` confirms 1, 2, 3 all
    available.
  - `test_igmp_default_version_unchanged_at_v2` pins the default
    so operators aren't surprised.
  - `test_igmpv1_dialog_payload_round_trips` confirms the dialog
    ships `version=1` in the body.

### Still open from the v0.2.81 audit
- **VRRP auth TLVs** (BLOCKER) — VRRPv2 auth_type/password +
  VRRPv3 auth-extension TLV. Next focused release.
- **PIM Join/Prune** (BLOCKER, roadmap) — full PIM-SM state machine.
- **POLISH items** — frame preview, per-row Stop button, sortable
  sessions table, Last Error column truncation.

### Test count
468 → 473 (+5).

## [0.2.81] - 2026-05-30

**L2 emulation audit fixes** — first round from a holistic audit of
the L2 emulation surface (LACP / LLDP / VRRP / IGMP / PIM / BFD,
last touched piecemeal from v0.2.38 through v0.2.74). Closes the 5
highest-impact PAIN items plus a server-doc cleanup. Three BLOCKER
items (IGMPv1, VRRP auth TLVs, PIM Join/Prune) defer to their own
focused releases; four POLISH items defer too.

### What changed

#### Validation (lifted out of scapy-deep-error-land)
- **`widgets/l2_emulation_tab.py`** — two new module-level helpers:
  - `_validate_mac(value)` — accepts `XX:XX:XX:XX:XX:XX` only;
    rejects dashes, bare hex, IP shapes, junk.
  - `_validate_ip(value, family="any"|"v4"|"v6")` — uses Python's
    `ipaddress` module; honours required family per call site.
  
  Wired into `_on_accept` for every protocol's MAC / IP fields
  (LACP system_mac, LLDP src_mac, VRRP virtual_ips + src_ip +
  src_mac, IGMP group + src_ip + src_mac, PIM src_ip + src_mac,
  BFD src_ip + dst_ip + src_mac + dst_mac). Typos now surface as
  `"Source MAC: '00:11:22:33:44:ZZ' isn't a valid MAC address — …"`
  in a dialog at submit time, not as opaque "invalid MAC" deep in
  the sessions table's Last Error column an hour later.

#### VRRP v2 + IPv6 mismatch rejected
- The backend silently reverted to v3 when an IPv6 virtual_ip was
  shipped with `version=2` — surprising, since the dialog accepts
  the combination as valid. Now rejected up-front with a clear
  message naming the RFCs (3768 vs 5798). The legitimate v3 + IPv6
  combo continues to work.

#### PIM generation_id bounds-check
- `0xFFFFFFFF` is the max per RFC 7761 §4.9.5. Values >= 2³² used
  to silently truncate inside scapy; now rejected at submit time
  with a message naming the RFC and the field width.

#### Tooltips
- **IGMP group** field — tooltip explains that `0.0.0.0` is the
  General Query group address (RFC 2236 §3); otherwise specify the
  group. The validation block accepts `0.0.0.0` despite the v4
  family check.
- **PIM generation_id** field — tooltip explains the 32-bit width
  and that the value should change every PIM-daemon restart.

#### Server doc cleanup
- **`server/l2_routes.py`** module docstring — added `bfd` to the
  supported-protocols list (it landed in v0.2.61 but was never
  added) and a design note explaining why there's no
  `/api/l2/<protocol>/stop` (one generic `/api/l2/stop` is by
  design — kind-agnostic).

### Tests
- **`tests/test_l2_dialog_validation.py`** — new file, 31 tests:
  - 11 parametrised MAC/IP validator unit tests (good + bad
    inputs).
  - Integration tests for each protocol's bad-input rejection
    (LACP MAC, BFD dst_ip, PIM src_mac, PIM gen_id overflow,
    VRRP v2+IPv6, VRRP v3+IPv6 sanity, IGMP `0.0.0.0` accepted).

### Deferred from the audit
- **IGMPv1 support** (BLOCKER) — needs scapy IGMPv1 wiring; own release.
- **VRRP auth TLVs** (BLOCKER) — VRRPv2 auth_type/password + VRRPv3
  auth-extension TLV; own release.
- **PIM Join/Prune** (BLOCKER, marked roadmap) — full PIM-SM state
  machine, big lift.
- **Frame preview** (POLISH) — hex dump + scapy.summary() before
  Start.
- **Per-row Stop button** (POLISH) — current Stop-selected /
  Stop-all UX is functional but takes 2 clicks instead of 1.
- **Sortable / filterable sessions table** (POLISH).
- **Last Error column truncation** (POLISH) — 120 → 200 chars or
  Details popup.

### Test count
437 → 468 (+31).

## [0.2.80] - 2026-05-30

**Help-guide self-audit corrections** — a second pass over the
What's New guide caught three small drifts that v0.2.79 itself
introduced or left behind.

### What changed
- **Stale test count** — guide footer claimed "431 tests" but the
  actual count after v0.2.79 is 436. Fixed (and there's a pinning
  test in `tests/test_help_dialogs.py` so the next stale period
  fails CI loudly).
- **Help menu order table out of sync with the actual menu** —
  the v0.2.79 doc update added Supported Features and What's New to
  the table but left them at the bottom. The real menu (from
  v0.2.73 onwards) puts them BEFORE the first separator, alongside
  Install Guide and API Guide. Table reordered to match; separators
  rendered explicitly so the visual matches the menu structure.
- **"Added 0.2.72" caption was wrong** — Supported Features content
  + menu entry both landed in v0.2.73 (v0.2.72 was the prior
  What's-New refresh that preceded it). Caption corrected to
  "Added 0.2.73".

### New test
- **`tests/test_help_dialogs.py::test_feature_guide_help_table_matches_menu_order`**
  uses string-position checks to pin the table order. If a future
  edit moves entries around without updating the table, the test
  fails immediately.

### Test count
436 → 437 (+1).

## [0.2.79] - 2026-05-30

**Help guide refresh** — catch What's New + Capabilities guides up
to the five releases shipped since v0.2.73 (preflight closeout,
DPDK closeout, loose-ends bundle). Same doc-drift problem v0.2.72
fixed; this is its sequel.

### What's New (`_FEATURE_GUIDE_HTML`) additions
Seven new sections covering 18 user-visible surfaces:

- **DPDK telemetry & admin** (new top-level §)
  - 0.2.75 pre-flight DPDK fallback warnings (Use-DPDK checkbox
    constraint tooltip + end-of-batch dialog).
  - 0.2.76 readiness chip in the status bar.
  - 0.2.76 NIC bind safety guards (mgmt iface / SSH / active stream)
    with the "Bind anyway" escape hatch.
  - 0.2.77 runtime DPDK fallback in the Engine column.
  - 0.2.77 hugepage allocation feedback (requested vs actual).
  - 0.2.77 inline Unbind button in DPDK Status.
- **Devices tab → Preflight surfaces** (new top-level §)
  - 0.2.70 → 0.2.71 preflight bar + Apply hook (was inline before).
  - 0.2.74 Export CSV/JSON + sortable Details.
  - 0.2.78 per-device dot + pill-click filter.
- **VXLAN sub-tab additions** (new top-level §)
  - 0.2.74 active-injections row tooltips.
  - 0.2.78 EVPN active-injections chip.
- **Streams tab additions** (new top-level §)
  - 0.2.78 SR-MPLS row badge.
- **L2 Emulation additions** (new top-level §)
  - 0.2.74 full BFD RFC 5880 field set (diag + echo RX).
- **Tools menu additions** (new top-level §)
  - 0.2.74 RFC 2544 timestamped export filenames.

Also: stale `315 tests` → `431 tests` in the reliability-fixes
footer.

### Capabilities (`_CAPABILITIES_GUIDE_HTML`) updates
- §7 Preflight: new "Findings surfaces" sub-block enumerating
  pills, per-device dot, pill-click filter, sortable Details +
  CSV/JSON export, auto-refresh after Apply.
- §9 Backends: DPDK caveat row updated to call out the surfaced
  fallback ("surfaced explicitly since 0.2.75"); new sub-block
  "DPDK fallback telemetry" covers both pre-flight and runtime
  channels with the API field names (`actual_engine`,
  `fallback_reason`, `runtime_engine`, `runtime_fallback_reason`).
  Per-core TX stats explicitly called out as the one deferred item.
- §10 Server/API: new "DPDK admin in the main GUI" sub-block
  enumerating the readiness chip, bind safety, hugepage feedback,
  inline Unbind, and EVPN active chip.

### Tests
- **`tests/test_help_dialogs.py`** — 5 new tests:
  - Version-tag check extended through 0.2.78.
  - `test_feature_guide_documents_dpdk_telemetry_surfaces`.
  - `test_feature_guide_documents_preflight_followups`.
  - `test_capabilities_guide_covers_preflight_findings_surfaces`.
  - `test_capabilities_guide_covers_dpdk_fallback_telemetry`.
  - `test_capabilities_guide_covers_dpdk_admin_surfaces`.

### Test count
431 → 436 (+5).

## [0.2.78] - 2026-05-30

**Preflight closeout** — clears the 4 remaining preflight follow-up
items from the v0.2.74 audit. Per-device dot in the Devices table,
clickable filter pills on the bar, EVPN active-count chip on the
VXLAN sub-tab, SR-MPLS row badge in the stream table.

### Per-device preflight dot in Devices table
- **`widgets/preflight_bar.py`** — gains a `by_device_updated`
  pyqtSignal emitted on every successful refresh; new
  `current_by_device()` returns a deep-copied snapshot for late
  subscribers (the dialog can mutate it without poisoning the bar's
  cache).
- **`widgets/devices_tab.py`** — new `_apply_preflight_dots`
  paints a red/amber/green dot in front of each Device Name cell
  driven by the bar's `by_device` payload. Severity wins
  (error > warning > clean). Re-applied after every table rebuild
  (pulled from `current_by_device`) so a refresh doesn't blank the
  dots. Idempotent — repaints strip the previous dot, no emoji
  pile-up. Tooltip rolls into the existing one.

### Pill-click filter on preflight bar
- **`widgets/preflight_bar.py`** — error + warning pills now have
  cursor=Pointing and a mousePressEvent that opens the Details
  dialog with a `level_filter` argument. OK pill is non-interactive
  (clean findings have no rows to filter).
- **`PreflightDetailsDialog`** — new `level_filter` kwarg
  pre-filters the findings list before population; title reflects
  the filter ("Preflight findings — errors only") and the hint
  text adds a "Close and reopen from Details… to see every
  finding" instruction.

### EVPN active-injections chip on VXLAN sub-tab
- **`widgets/evpn_active_chip.py`** — new module. `EvpnActiveChip`
  polls `/api/evpn/type2/list` (which returns both kinds since
  v0.2.67) every 30 s and shows **⚡ EVPN: N active** in violet
  (with the count) or **⚡ EVPN: idle** in gray. Click emits a
  `clicked()` signal so the host opens the EVPN Inject dialog —
  operators no longer have to open the dialog just to check whether
  anything's running.
- **`utils/devices_tab_vxlan.py`** — chip wired into the VXLAN
  sub-tab action bar (right side after `addStretch`), try/except
  guarded, hooked to `_open_evpn_inject_dialog`.

### SR-MPLS row badge in stream table
- **`traffic_client/server_section.py`** — Details cell renderer
  appends `[MPLS ×N]` (when ≥2 labels), `[MPLS]` (single label or
  legacy `mpls_label` field), or nothing. None / "" / 0 explicitly
  filtered out so trailing nulls don't over-count. Operators can
  scan the table for MPLS streams without opening each editor.

### Tests
- **`tests/test_preflight_bar.py`** — +5 tests covering the
  by_device signal, deep-copy snapshot semantics, missing-by_device
  payload graceful path, and PreflightDetailsDialog level filter
  (filter applies + windowTitle reflects + no-filter still works).
- **`tests/test_evpn_active_chip.py`** — new file, 7 tests pinning
  the chip's idle/active states, count rendering, alt-key fallback
  (`items` vs `injections`), silent-on-HTTP-failure, silent-on-503,
  clicked signal emission, no-server graceful path.
- **`tests/test_sr_mpls_badge.py`** — new file, 9 tests pinning the
  badge formatter (no MPLS → empty, legacy label → `[MPLS]`,
  modern stack → `[MPLS ×N]`, trailing-null filtering, modern wins
  over legacy).
- **`tests/test_devices_preflight_dot.py`** — new file, 7 tests
  covering severity-wins classification, idempotent strip + repaint,
  and the round-trip from `PreflightBar.by_device_updated` to a
  subscriber callable.

### Both audits now closed
With v0.2.78, every item from the v0.2.74 GUI audit and the v0.2.75
DPDK audit is either shipped (v0.2.74→v0.2.78) or explicitly
deferred (per-core TX stats — requires tx_worker.c change + binary
rebuild, bundled with future C work).

### Test count
404 → 431 (+27).

## [0.2.77] - 2026-05-30

**DPDK closeout** — clears the remaining items from the v0.2.75 DPDK
audit. Runtime fallback now surfaces to the GUI; Line-Rate auto-pick
is no longer invisible; hugepage allocation reports actual-vs-
requested; status carries ABI version indicators; bound interfaces
gain inline Unbind buttons.

### Runtime fallback telemetry (audit BLOCKER #2, deferred from v0.2.75)
Until now, when the launcher had to swap engines mid-flight
(tx_worker rc=100 Broadcom ULP error, exception during DPDK
handoff), the swap was logged server-side and invisible to the
operator. Now:

- **`multithreaded_traffic_gen.py`** — new
  `StreamTracker.mark_runtime_engine(iface, sid, runtime_engine=,
  fallback_reason=)` records the swap on the tracked stream;
  `get_stream_stats` surfaces `runtime_engine` +
  `runtime_fallback_reason` (optional fields, omitted when never set
  so the legacy stats shape stays clean). The Broadcom ULP rc=100
  path and the catch-all exception-handler both call `mark_*`.
- **`traffic_client/statistics_section.py`** — Engine column renders
  **"Scapy ⚠ (was DPDK)"** in amber when the stream got swapped
  mid-flight; cell tooltip carries the reason verbatim. Operators
  stop wondering why throughput halved without grep'ing journalctl.

### Line-Rate auto-pick echoed in start response (PAIN #4 + POLISH #10)
- **`utils/dpdk_tx_worker.py`** — new pure-function
  `resolve_actual_tx_cores(stream, iface)` returns `(value,
  was_auto_picked)`. Mirrors the in-worker auto-pick logic so the
  start endpoint can synchronously include the chosen value.
- **`run_tgen_server.py`** — `/api/traffic/start` decorates DPDK
  entries with `actual_tx_cores` + `tx_cores_auto_picked`.
- **`traffic_client/stream_logic.py`** — logs "Line-Rate auto-picked
  tx_cores=8 for 'stream-name'" so the operator confirms the engine
  ran multi-queue (no modal — the bump is desired, not warning-worthy).

### Hugepage allocation feedback (PAIN #6)
- **`run_tgen_server.py`** — `/api/dpdk/hugepages` re-reads the
  sysfs file after writing and returns `requested` +
  `actual_allocated` so the client can spot kernel-capped requests.
- **`traffic_client/dpdk_menu_actions.py`** — toast now reads
  **"Allocated 4096 × 2MB on srv01"** or, when partial,
  **"⚠ Requested 8192, Actually allocated 4096"** with a hint
  about memory fragmentation. Pre-0.2.77 servers fall back to the
  legacy "configured successfully: N" message.

### tx_worker / DPDK ABI version (POLISH #11)
- **`run_tgen_server.py`** — `/api/dpdk/status` includes
  `dpdk_version` (`pkg-config --modversion libdpdk`) +
  `tx_worker_built` (binary mtime). Catches the silent-crash class
  where libdpdk got upgraded but tx_worker wasn't rebuilt.
- **`widgets/dpdk_readiness_chip.py`** — tooltip rows show
  `DPDK libraries: ok (v23.11.0)` + `tx_worker binary: ok (built
  2026-05-15 14:32)`. Operator scans for ABI drift at a glance.

### Inline Unbind button in DPDK Status dialog (POLISH #12)
- **`traffic_client/dpdk_menu_actions.py`** — the Status dialog now
  enumerates every vfio-pci-bound interface under a "Bound to DPDK
  (vfio-pci):" header with a per-row **Unbind** button. No more
  trip to Tools → Unbind Interface; the action lives where the
  listing is.

### Deferred (still open)
- **Per-core TX stats exposure** (POLISH #9) — requires modifying
  `tx_worker.c` to emit `STAT_Q: stream=<id> queue=N tx=<count>`
  lines + rebuilding the binary on every deployed server. Pure-
  Python release scope only; bundled with other tx_worker C changes
  (MPLS/QinQ support) in a focused follow-up.

### Tests
- **`tests/test_dpdk_engine_resolver.py`** — +4 tests covering
  `resolve_actual_tx_cores` (explicit value honoured, non-Line-Rate
  defaults to 1, missing iface graceful, unknown iface graceful).
- **`tests/test_dpdk_readiness_chip.py`** — +3 tests covering
  `dpdk_version` + `tx_worker_built` in the tooltip + the legacy-
  payload graceful path (no "None" leakage).
- **`tests/test_dpdk_runtime_fallback.py`** — new file, 5 tests
  pinning `mark_runtime_engine` semantics + the stats payload
  shape (fields omitted unless marked, present when marked).

### Test count
392 → 404 (+12).

## [0.2.76] - 2026-05-30

**DPDK readiness chip + NIC-bind safety guards.** Closes the last
BLOCKER from the v0.2.75 DPDK audit (#3) and the riskiest PAIN (#5)
in one focused release.

### What changed

#### DPDK readiness chip (audit BLOCKER #3)
A small colour-coded indicator now lives in the QMainWindow status
bar (right-aligned, always visible). Polls `/api/dpdk/status` every
30 s and shows:

* **green** — DPDK ready (libdpdk + tx_worker + hugepages + IOMMU +
  vfio-pci all present).
* **amber** — degraded (libdpdk + tx_worker present, but missing
  hugepages / IOMMU / vfio). Mellanox / mlx5 NICs still work; others
  won't. Tooltip explains.
* **red** — unusable (tx_worker binary or libdpdk missing).
  Enabling Use-DPDK guarantees a fallback.
* **gray** — unknown (no server selected or HTTP failure).

Hover the chip to see each subsystem's individual state — answers
"why amber?" without opening a dialog. Defensively quiet on flaky
links: holds previous state, logs a debug line, never modal-alerts.

#### NIC-bind safety guards (audit PAIN #5)
`/api/dpdk/bind` now pre-flights the bind and refuses (HTTP 409)
when the candidate interface:

* **carries the default route** — binding it would drop kernel
  networking and lock the operator out of the host;
* **is the SSH session's interface** — same outcome, different
  detection path (parses `$SSH_CLIENT`);
* **has an active traffic stream running on it** — would kill the
  test mid-flight.

The client surfaces the reason verbatim in a modal with a
**"Bind anyway"** escape hatch (re-posts with `force=true`) so
operators who *really* mean it (e.g. binding from console with a
spare NIC for SSH) aren't blocked. Refusal is logged server-side
too. Safety check failure itself is non-fatal — `ip route` parse
errors won't lock anyone out.

### What changed (files)
- **`widgets/dpdk_readiness_chip.py`** — new module. `classify_dpdk_status`
  (pure function: payload → state/headline/tooltip) + `DpdkReadinessChip`
  QLabel widget with 30 s timer + manual `refresh()`. Modeled on
  `widgets/preflight_bar.py` for visual consistency and the same
  "defensively quiet on HTTP failure" contract.
- **`utils/dpdk_bind_safety.py`** — new module. Pure-function
  `check_bind_safe(iface, default_route_iface, ssh_client_iface,
  active_stream_ifaces)` returns None or refusal-reason string.
  Helpers `collect_default_route_iface(run=…)` and
  `collect_ssh_client_iface(env, run=…)` accept an injectable `run`
  callable so subprocess calls are mockable in tests.
- **`traffic_client/main.py`** — `DpdkReadinessChip` instantiated
  and added to `statusBar().addPermanentWidget(…)` during main
  window init, wrapped in try/except so a construction failure
  can't block the window.
- **`run_tgen_server.py`** — `/api/dpdk/bind` calls `check_bind_safe`
  with snapshots of default route + SSH session iface +
  `stream_tracker.get_stream_stats()`. Returns 409 +
  `{error, code: "BIND_UNSAFE", interface, can_force: true}` when
  unsafe and `force` flag is not set.
- **`traffic_client/dpdk_menu_actions.py`** — `_handle_bind_result`
  short-circuits on 409 + `code=BIND_UNSAFE`: shows the refusal
  reason in a QMessageBox.Warning with a destructive-role
  "Bind anyway" button that re-posts with `force=true`.

### Tests
- **`tests/test_dpdk_readiness_chip.py`** — 14 tests covering all 8
  classify branches (green/amber/red across 5 subsystem combos +
  edge cases) plus the Qt smoke tests (initial gray, green-on-ready,
  red-on-tx-worker-missing, silent-on-HTTP-failure, silent-on-503).
- **`tests/test_dpdk_bind_safety.py`** — 14 tests covering every
  refusal combo (mgmt-iface via default-route, mgmt-iface via SSH,
  active-stream, none, both — and the priority order), whitespace
  handling, empty-iface no-crash, plus 6 tests for the two
  collector helpers including subprocess-error suppression.

### Deferred from the DPDK audit
* PAIN #4 — line-rate auto-pick result invisible (needs the start
  endpoint to echo back the chosen `tx_cores`)
* PAIN #6 — hugepage allocation feedback toast
* Runtime fallback telemetry (BLOCKER #2 deferred from v0.2.75)
* POLISH #9–#12 — per-core stats, version indicator, inline Unbind

### Test count
364 → 392 (+28).

## [0.2.75] - 2026-05-30

**DPDK fallback telemetry** — close the silent-fallback gap cluster
the v0.2.74 audit surfaced. Operators who enabled "Use DPDK" on a
TCP / IPv6 / MPLS / QinQ stream used to watch it "run" at Scapy
speed with no clue why; now the start endpoint pre-flights the
decision and the GUI surfaces the reason in a single end-of-batch
dialog.

### What changed

#### Backend
- **`utils/dpdk_tx_worker.py`** — new pure-function helpers:
  - `dpdk_compatibility_check(stream_data) -> Optional[str]` returns
    a human-readable reason if the stream can't run on `tx_worker`
    (TCP / ICMP / IPv6 / MPLS stack / legacy single MPLS label /
    QinQ outer VLAN); returns `None` if compatible.
  - `resolve_engine(stream_data) -> (engine, fallback_reason)`
    wraps `should_use_dpdk` + the compat check into the
    "(scapy|dpdk), reason" tuple the start endpoint hands back.
- **`run_tgen_server.py`** — `/api/traffic/start` calls
  `resolve_engine` per stream and decorates every entry in the
  `started_streams` response with `actual_engine` and (when
  fallback happens) `fallback_reason`. Logged server-side too so
  the journal carries the same trail.

#### Client
- **`traffic_client/stream_logic.py`** — collects every
  `fallback_reason` across the Start batch and shows ONE
  consolidated `QMessageBox.information` at the end naming each
  affected stream + reason. Logged at INFO.
- **`widgets/stream_dialog.py`** — DPDK checkbox tooltip rewritten
  to spell out the supported envelope (IPv4 + UDP + ≤1 VLAN tag +
  no MPLS) so operators see the constraint at point-of-use, not
  buried in the workflow guide.

#### Tests
- **`tests/test_dpdk_engine_resolver.py`** — new file, 26 pure-
  function tests pinning every compatibility rule + the engine
  decision matrix (parametrised across all 7 known-incompat
  combos).

### Limitations
This release covers **pre-flight** fallback (decisions made before
the worker thread starts). **Runtime** fallback (tx_worker rc=100
Broadcom ULP error, NIC link drop mid-stream, OOM) still goes
through the worker thread's existing logging path and isn't yet
surfaced to the GUI — that needs an async telemetry channel through
the stats endpoint, deferred to a focused follow-up.

The other DPDK audit items also deferred:
* No DPDK-readiness chip in the main window status bar (BLOCKER #3)
* No NIC-bind safety guards (mgmt interface, active stream detection)
* No hugepage allocation feedback
* No per-core TX stats, ABI version indicator, inline Unbind button

### Test count
338 → 364 (+26).

## [0.2.74] - 2026-05-30

**Loose-ends bundle from the v0.2.62→v0.2.73 audit.** Triaged 18
gaps the audit surfaced; this release closes the 3 confirmed bugs +
3 cheap polish items. The bigger UX changes (per-device preflight
dot in Devices table, EVPN active-count chip on VXLAN sub-tab,
pill-click filter, SR-MPLS badge) are deferred to focused
follow-up releases.

### Bug fixes
- **Preflight bar now refreshes after BGP / OSPF / IS-IS / VXLAN
  apply.** v0.2.71 wired `kick_refresh` into the device-level Apply
  paths but missed the four protocol-specific Apply buttons.
  Operators tweaking just BGP got the same 60 s wait the v0.2.71
  hook was supposed to eliminate.
  Pinned by a parametrised regression test in
  `tests/test_preflight_bar.py::test_protocol_apply_paths_call_kick_refresh`.
- **Preflight Details modal is sortable.** `setSortingEnabled` was
  hard-coded `False`; header clicks did nothing. Now sorted by Level
  ascending on open (errors on top) and operators can click any
  header to re-group by Device or Code.
- **Full BFD RFC 5880 field set exposed in the L2 emulation GUI.**
  Backend `start_bfd` accepted `diag` (RFC 5880 §4.1 diagnostic
  code) and `required_min_echo_rx_us` as kwargs since v0.2.61, but
  the GUI silently defaulted both to 0. Added:
  - `Diagnostic:` QComboBox with the 9 named RFC 5880 §4.1 codes
    (defaults to 0 — No Diagnostic).
  - `Required Min Echo RX:` µs spinner (defaults to 0 — echo
    function disabled).

### Polish
- **Export findings (CSV + JSON) on the preflight Details modal.**
  Two new buttons next to Close. Defaults to a timestamped filename
  (`preflight_findings_2026-05-30_14-35-22.csv`) so the operator
  hits Save without typing.
- **Per-row tooltips on the EVPN active-injections table.** Hover
  any cell to see Type-2/5 kind, count, iface, base MAC / prefix /
  VTEP / VRF table at a glance — no need to open Details to know
  what's running.
- **Timestamped default filenames for RFC 2544 exports** (CSV was
  missing one; HTML had one in a different format). Both now use
  the same `YYYY-MM-DD_HH-MM-SS` convention as the preflight export
  so they sort naturally next to each other on disk.

### What changed
- **`widgets/devices_tab.py`** — `apply_bgp_configurations`,
  `apply_ospf_configurations`, `apply_isis_configurations`,
  `apply_vxlan_configurations` all call `kick_refresh(self)` after
  the handler returns.
- **`widgets/preflight_bar.py`** — `setSortingEnabled(True)` + sort
  by Level ascending after population; sort indicator visible. New
  `_export_csv` / `_export_json` / `_default_filename` helpers +
  Export buttons in the dialog footer. Standard library imports
  added (`csv`, `datetime`, `json`).
- **`widgets/l2_emulation_tab.py`** — `_build_bfd_panel` gains
  `_bfd_diag` (QComboBox of RFC 5880 codes) + `_bfd_echo_rx_us`
  (µs spinner). `_on_accept` carries both into the body.
- **`widgets/evpn_inject_dialog.py`** — new `_row_tooltip` static
  helper builds an HTML tip from the inject record. Applied to
  every populated cell in each row.
- **`widgets/rfc2544_dialog.py`** — CSV default filename is now
  timestamped; HTML reformatted to match.
- **`tests/test_preflight_bar.py`** — parametrised hook-check
  across the 4 protocol apply methods (source-grep, no need to
  stand up the full tab); `setSortingEnabled` pinned in the
  existing dialog test; 4 new tests for the export buttons.
- **`tests/test_l2_emulation_bfd.py`** — new file. 4 tests pinning
  the BFD GUI: diag combo has all 9 codes + defaults to 0, echo-RX
  spinner present + defaults to 0, both round-trip through
  `_on_accept` payload.
- **`tests/test_evpn_inject_dialog.py`** — new
  `test_active_row_tooltip_summarises_inject_params` covers
  Type-2 + Type-5 tip content and that every populated cell carries
  the same tip.
- **`tests/test_rfc2544_dialog.py`** — new
  `test_export_default_filenames_are_timestamped` covers both CSV
  and HTML exports.

### Test count
324 → 338 (+14).

## [0.2.73] - 2026-05-30

**Supported Features (Capabilities Guide)** — the comprehensive
capability matrix as its own Help menu entry. Operator opened
Help → What's New looking for "everything this app can do" and
correctly noted the existing dialog is a changelog, not a capability
reference.

### Three distinct Help surfaces now

| Dialog | Question it answers |
|---|---|
| Supported Features (new) | "Can the app do X?" |
| What's New | "What changed in 0.2.N?" |
| API Guide | "What's the curl command?" |

### What the new guide covers
Ten sections, each with the surface (tab / menu / endpoint) where the
feature lives:
1. **Stream packet builder** — L2 framing variants (untagged / Dot1Q /
   QinQ), L3 (IPv4/IPv6), L4 (UDP/TCP/ICMP/IGMP), encapsulations
   (MPLS / SR-MPLS stack / VXLAN), frame sizing (fixed / random /
   IMIX), payload modes, NLAT timestamps. Scapy-vs-DPDK matrix per
   protocol.
2. **L2 / Multicast emulation** — LACP, LLDP, VRRP, IGMP, PIM, BFD
   with RFC references.
3. **Routing / control plane** — BGP, BGP EVPN, OSPF, IS-IS, VRF.
4. **VXLAN / EVPN** — tunnels, Type-2 + Type-5 bulk inject, kind-
   tagged active table.
5. **DHCP** — v4/v6 × server/client matrix.
6. **Compliance** — RFC 2544 throughput + latency, HTML report.
7. **Preflight** — every current finding code with severity.
8. **Statistics + reporting** — counters, percentiles, CSV / HTML
   export.
9. **Backends** — Scapy vs DPDK with the UDP-only caveat called out
   explicitly.
10. **Server / API / operations** — multi-server, auth roles,
    install / upgrade paths, DPDK admin portal.

Plus a **"What this app is not"** section so operators don't waste
time hunting GRE / GTP / SRv6 / RFC 2889 — explicitly listed as
out-of-scope today.

### What changed
- **`widgets/stream_dialog.py`** — new `_CAPABILITIES_GUIDE_HTML`
  constant (~250 lines of structured HTML with tables, where-
  pointers, RFC numbers, Scapy/DPDK matrix) + `show_capabilities_guide`
  opener. Existing What's-New Help-table row updated to point at the
  new entry.
- **`traffic_client/main.py`** — new "Supported Features..." QAction
  in the Help menu (placed before "What's New" so the broader
  capability question comes first); new `show_capabilities_guide`
  slot method.
- **`tests/test_help_dialogs.py`** — 8 new tests pinning the section
  list, packet-layer protocols, L2 emulators, routing protocols,
  every preflight code, the DPDK UDP-only caveat, the "what we don't
  support" honesty section, and the Qt smoke-open.

### Test count
316 → 324 (+8).

## [0.2.72] - 2026-05-30

**What's-New guide catches up to the preflight bar** — operator
opened Help → What's New looking for documentation on the new pills
in the Devices tab and found nothing. The v0.2.69 guide stopped at
"the GUI bar is the next slice" and was never updated when v0.2.70 /
v0.2.71 actually shipped.

### What changed
- **`widgets/stream_dialog.py`** — new dedicated **"Devices tab →
  Preflight bar"** section in `_FEATURE_GUIDE_HTML`:
  - 0.2.70 entry covers the three pills, severity tinting, Details
    modal, ↻ button, auto-poll cadence, the current check codes,
    and the heads-up-not-a-gate framing.
  - 0.2.71 entry covers the immediate-refresh-after-Apply hook.
  - The existing 0.2.68 endpoint entry reframed as "the JSON
    backend behind the bar above" instead of "the GUI is the next
    slice".
  - Test-count line updated from "284+ tests" (stale since 0.2.65)
    to "315 tests".
- **`tests/test_help_dialogs.py`** — `0.2.70` + `0.2.71` added to the
  version-tag check; new `test_feature_guide_documents_preflight_bar`
  pins the section header + key behaviours so the next guide edit
  can't silently regress them.

### Test count
315 → 316 (+1).

## [0.2.71] - 2026-05-30

**Preflight bar refreshes immediately after Apply** — close the
60 s feedback gap between committing an edit and seeing whether it
introduced (or cleared) a finding.

### What changed
- **`widgets/preflight_bar.py`** — new module-level helper
  `kick_refresh(host, attr="preflight_bar")` that calls
  `host.<attr>.refresh()` guarded with hasattr + try/except. Returns
  True iff the refresh actually fired, so tests can assert the hook
  ran without watching the network.
- **`widgets/devices_tab.py`** — `_on_device_apply_finished` (single-
  device DB apply) and `_on_multi_device_apply_finished` (the main
  toolbar Apply path via MultiDeviceApplyWorker) both call
  `kick_refresh(self)` at the end. Fires on success and failure —
  operators want "all clean" confirmation too, not only new
  findings.
- **`tests/test_preflight_bar.py`** — 4 new tests for `kick_refresh`:
  fires when bar present, no-op when absent, swallows refresh
  exceptions, honours custom attr name.

### Why a helper instead of inline `self.preflight_bar.refresh()`
The Devices tab wraps the bar construction in try/except (so a bar
build failure can't block the tab from rendering), which means every
caller would have to repeat the hasattr + try/except dance. A
3-line helper makes the call sites one-liners and gives us a single
place to unit-test the guards.

### Test count
311 → 315 (+4).

## [0.2.70] - 2026-05-30

**Preflight findings bar** — the GUI front end for the preflight
backend that shipped in 0.2.68. A thin colour-coded strip at the top
of the Devices sub-tab tells operators at a glance whether their
config is BGP/EVPN-shaped before they hit Apply.

### What it does
- Self-contained `PreflightBar` widget docks above the Devices filter
  row. Polls `/api/preflight/check` every 60 s, also exposes a manual
  `↻` refresh button, and external code (e.g. post-Apply hooks) can
  call `refresh()` to update on demand.
- Three pills — `● N errors · ● N warnings · ● N OK` — coloured red /
  amber / green and muted when the count is zero. Pills get singular
  / plural right ("1 error", "2 errors") because operators notice
  when they don't.
- Bar background tints by worst severity: red on any error, amber on
  warning, green when clean. Subtle, doesn't drown out the table.
- **Details…** button opens a modal table (Level / Code / Device /
  Interface / Message), level cells colour-coded, sortable. Missing
  interface renders as em-dash so no "None" leaks visually.
- **Defensively quiet** — HTTP exception, non-200, malformed JSON,
  empty server URL all leave the previous pill values intact and log
  a debug line. No modal alerts on a flaky link.

### What changed
- **`widgets/preflight_bar.py`** — new module. `PreflightBar` (QFrame)
  + `PreflightDetailsDialog` (QDialog) + pill helpers + colour
  palette. ~300 LOC.
- **`widgets/devices_tab.py`** — `setup_devices_subtab` instantiates
  the bar as the first widget above the filter row, wrapped in
  try/except so a construction failure can't block the Devices tab
  rendering.
- **`tests/test_preflight_bar.py`** — 13 new tests: summary →
  pill text + bar tint, singular/plural copy, details-button enable
  state, refresh silent on exception / no-URL / 5xx, refresh
  populates from 200, timer on/off by interval, dialog row count +
  per-level colours.

### Reliability note
The bar resolves the server URL on every refresh via the provider
callback (`main_window.get_server_url`) rather than caching it at
construction, so changing the active server in the chassis picker
just works without a signal hookup.

### Test count
298 → 311 (+13).

## [0.2.69] - 2026-05-30

**Help menu refresh** — update the existing API Guide with everything
shipped since 0.2.41, and add a new "What's New" feature guide that
tells operators where to click for each new GUI surface.

### API Guide (Help → API Guide) — content additions
- **Endpoint summary table** gains rows for all the new endpoints:
  - EVPN bulk inject (`/api/evpn/type2/inject` / `clear` /
    `type5/inject` / `type5/clear` / `type2/list` — unified for both
    kinds).
  - Preflight (`/api/preflight/check`).
  - L2 row updated to advertise `bfd` alongside the existing 5
    protocols and to call out inline QinQ support.
- **New §23 callouts**: BFD added to the supported-protocols table
  with RFC 5880/5881 + TTL=255 single-hop note; QinQ paragraph
  describes the inner/outer encoding (`0x88a8` / `0x8100`) and the
  payload-ethertype-on-inner contract. New BFD + QinQ curl examples
  in the Start-examples block.
- **New §24 — EVPN bulk inject**: Type-2 + Type-5 with copy-pasteable
  curl blocks (inject + clear + the unified list endpoint), plus the
  cross-kind-safety warning about calling the wrong /clear endpoint.
- **New §25 — Preflight checks**: full response-shape example, the
  current finding-code list, and the "heads-up surface, not a gate"
  framing.
- **New §26 — RFC 2544 latency + HTML report**: `capture_latency`
  opt-in, the latency dict shape, the p95 addition, and the HTML
  report's Print → Save-as-PDF deliverable workflow.
- **New §27 — SR-MPLS label stack**: `mpls_labels` config shape,
  ethertype `0x8847`, BOS-bit auto-handling, and the legacy single-
  label back-compat guarantee.

### New: "What's New" feature guide (Help → What's New)
First feature-guide menu entry. Organised by where the change lives
(Streams tab / L2 Emulation / VXLAN sub-tab / Stats dock / Tools menu
/ Server-side / Reliability fixes), with a **"Where:" pointer** at
the end of each section so the operator knows which tab + button to
click. Every entry is tagged with the version it shipped in (green
chip for new features, amber chip for reliability fixes).

Sections covered: Streams chip (0.2.46); SR-MPLS stack field (0.2.65);
2 s flicker fix (0.2.57); L2 redesign (0.2.41 → 0.2.45); QinQ
(0.2.60); BFD (0.2.61); EVPN inject GUI (0.2.62 → 0.2.67); latency
p95 + CSV export (0.2.58); RFC 2544 latency + HTML report (0.2.59);
Preflight endpoint (0.2.68); Streams-table interaction fixes
(0.2.49 → 0.2.55).

### What changed
- **`widgets/stream_dialog.py`** — `_API_GUIDE_HTML` extended with
  the new rows + 4 new sections (§24–§27); new `_FEATURE_GUIDE_HTML`
  constant; new `show_feature_guide(parent)` function alongside the
  existing `show_api_guide` / `show_install_guide` / etc.
- **`traffic_client/main.py`** — new "What's New..." entry in the
  Help menu (between API Guide and the separator); new
  `show_feature_guide` method routes through the dialog opener.

### Verified — 14 new tests, full suite 298 passing
`tests/test_help_dialogs.py`:
- Pure-string content pins on the API guide: every new endpoint URL
  present; L2 row carries BFD; QinQ / `outer_vlan_id` / `0x88a8`
  mentioned; dedicated `<h2>` sections §24–§27 exist; BFD curl
  example present.
- Pure-string content pins on the Feature guide: every expected
  section heading present; every version chip from 0.2.41 → 0.2.68
  present; "Where:" pointer convention used at least 6 times;
  cross-reference to the API Guide present.
- Qt smoke: opening each guide builds a `QTextBrowser` and populates
  it from the right constant (verified by intercepting `setHtml`).

### Notes
- Client-only addition; no server change.

## [0.2.68] - 2026-05-30

**Preflight checks** — surface common bad-config shapes BEFORE Apply
so the operator doesn't debug failures after the round-trip. Backend
only this release; GUI integration (a bar at the top of the Devices
tab) lands in 0.2.69.

### Findings caught today
The codebase had log lines for several of these (e.g.
``utils/bgp.py:1473`` logs ``"VXLAN config found but missing required
fields"``); they're now visible BEFORE the Apply round-trip:

  * ``BGP_NO_REMOTE_ASN`` (error) — BGP protocol enabled but
    ``bgp_config.bgp_remote_asn`` is empty. No session will form.
  * ``BGP_NO_LOOPBACK`` (warning) — BGP enabled but Loopback IPv4 is
    empty. FRR's router-id falls back to a transient interface
    address; works, but brittle.
  * ``VXLAN_MISSING_FIELDS`` (error) — vxlan_config tunnel missing
    one or more of vni / local_ip / remote_ip. FRR EVPN will skip it.
  * ``VXLAN_EMPTY`` (warning) — vxlan_config present but no usable
    tunnels.
  * ``OSPF_NO_AREA`` / ``ISIS_NO_AREA`` (warning) — adjacency won't
    form. Also catches the IS-IS-monitor-auto-stop case from
    ``utils/devices_tab_isis.py``'s ``has_real_isis_config``.
  * ``DUPLICATE_IPV4`` (error) — cross-device check. Same IPv4 on two
    devices in the deployment wedges forwarding and flaps ARP.

### What changed
- **`utils/preflight.py`** (new): every check is a pure function
  taking a device dict (+ all-devices list for cross-device checks)
  and returning ``List[Finding]``. Findings carry ``level``, ``code``,
  ``message``, ``device_name``, ``interface`` — stable shape the GUI
  can render and tests pin. ``check_all_devices(devices)`` aggregates
  into ``{summary: {error,warning,ok,total}, findings: [...],
  by_device: {name: [...]}}`` ready for ``jsonify``.
- **`run_tgen_server.py`**: new ``GET /api/preflight/check``
  (viewer-gated) — reads the local device DB and returns the
  aggregated report. Defensive: empty DB → all-zero summary, never
  500s.

### Verified — 26 new tests, full suite 284 passing
Per-check coverage (happy / sad / skip-when-protocol-absent) + the
cross-device case (unique / two-way duplicates / CIDR-stripped
match / both-empty doesn't collide). Aggregator: groups by device,
counts levels right, runs cross-device checks, handles empty
deployment, and the finding shape is pinned for the GUI.

### Notes
- Operator-friendly: this is a **heads-up surface**, NOT a gate.
  Apply still runs whether there are findings or not; the GUI just
  shows them so the operator can choose to address them first.
- Adding new checks is one-function + one test — purely additive.

## [0.2.67] - 2026-05-30

**EVPN Type-5 inject GUI** — completes v0.2.66. The dialog now has a
tab selector and a Type-5 (IP Prefix) form alongside the existing
Type-2 (MAC/IP) form; the per-row Clear button is **kind-aware** and
routes to the matching `/api/evpn/{kind}/clear` endpoint.

### What changed
- **`widgets/evpn_inject_dialog.py`** — restructured around a
  `QTabWidget`:
  - **Tab 1 — "Type-2 (MAC/IP)"**: existing v0.2.63 form unchanged
    (every attribute name preserved so the v0.2.63 tests still hold —
    `iface_field`, `base_mac_field`, `count_spin`, …, `inject_btn`).
  - **Tab 2 — "Type-5 (IP Prefix)"**: new form with `dev_field`,
    `base_prefix_field`, `prefix_len_spin`, `count_t5_spin`,
    `gateway_field`, `vrf_table_spin` (special text "main" at 0), and
    its own `inject_btn_t5`.
  - Shared `status_label` below the tabs — both Inject buttons write
    to the same green/amber/red status line; success messages
    identify the kind so the user can tell which tab fired.
  - Active-injections table grew a **Kind** column (color-coded:
    Type-2 blue, Type-5 violet); the per-row Clear button routes to
    `/api/evpn/type2/clear` or `/api/evpn/type5/clear` based on the
    row's kind via a new `_row_kinds` cache populated by
    `_populate_active`.
  - `_clear_one(inject_id, kind=None)` — when called without `kind`,
    looks it up in the cache and falls back to `"type2"` so the
    v0.2.63 test `test_clear_one_posts_inject_id` (which calls with
    no cache) still hits the Type-2 endpoint.

### Verified — 12 new tests + 14 v0.2.63 tests still passing; full suite 258
`tests/test_evpn_inject_dialog_type5.py`:
- Tabs exist with expected labels; both inject buttons present on `self`.
- Type-5 payload: minimal / with gateway / `vrf_table=0` omitted vs
  non-zero passed through / missing required → None.
- `_on_inject_t5` posts to `/api/evpn/type5/inject`; status message
  mentions "Type-5"; partial failure goes amber.
- Active table renders a Kind column (Type-2 / Type-5); legacy rows
  with no `kind` field default to Type-2 (back-compat with pre-0.2.66
  servers).
- **Per-row Clear routes correctly**: type-5 row → `/type5/clear`,
  type-2 row → `/type2/clear` (the critical contract — wrong routing
  would leak kernel state via the wrong cleaner). Direct
  `_clear_one(id)` call with no populated cache still defaults to
  `/type2/clear` (v0.2.63 test contract intact).

### Notes
- Client-only; no server change. v0.2.66's endpoints power it.
- The fix in this slice (VRF table id capped to a sane signed-32-bit
  range, not the unsigned-32-bit kernel max) caught itself in tests —
  the dialog wouldn't even construct against the larger range because
  QSpinBox is signed.

## [0.2.66] - 2026-05-30

**EVPN Type-5 (IP Prefix) bulk injection** — sibling of v0.2.62's
Type-2 inject. Synthesise N IPv4 prefixes as kernel routes; FRR
(`address-family l2vpn evpn` + `advertise ipv4 unicast` under the VRF)
picks them up and advertises them as EVPN Type-5.

### What changed
- **`utils/evpn_inject.py`** — extended (not replaced) with:
  - `generate_prefix_range_v4(base_prefix, prefix_len, count)` — strict
    alignment validation (refuses misaligned bases up front so the
    operator sees the typo).
  - `build_route_inject_commands(prefixes, dev, gateway, vrf_table)`
    and `build_route_clear_commands(...)` — pure command-list builders.
    Clear deliberately omits `via` (kernel matches by prefix + table
    on delete).
  - `inject_type5(...)` and `clear_type5(...)` — same shape as the
    Type-2 entry points; injectable `run` for tests.
  - Registry now tags every record with ``kind`` (``"type2"`` |
    ``"type5"``). `clear_type2` defensively refuses a type-5 record
    (and vice versa) and puts it back so the right cleaner can find
    it — protects against the wrong cleaner building wrong commands
    and leaking kernel state.
  - `list_active_injections` returns ``kind`` + protocol-specific
    summary fields, plus a cross-kind ``iface``/``count`` alias so the
    v0.2.63 GUI table renders type-5 rows cleanly without changes.
- **`run_tgen_server.py`** — two new operator-gated endpoints:
  - ``POST /api/evpn/type5/inject``
  - ``POST /api/evpn/type5/clear``
  The existing ``GET /api/evpn/type2/list`` already returns both
  kinds (it's the unified list).

### Verified — 15 new tests, full suite 246 passing
`tests/test_evpn_type5_inject.py`:
- Prefix range arithmetic: /24 across octet boundary; /28 step of 16;
  zero count → empty; misaligned base raises; out-of-range prefix_len
  raises (1..32).
- Command-list builders: minimal `ip route add`; with-gateway-and-VRF
  flag passthrough; clear omits `via` and keeps `table`; clear omits
  `dev` when None.
- `inject_type5`: one command per prefix; gateway/vrf threaded
  through; partial failure surfaces per-command errors; zero count
  raises.
- `clear_type5`: drops record even when kernel "no such route" errors;
  unknown id returns warning not 500.
- Cross-kind safety: `list_active_injections` includes both kinds with
  a stable alias; `clear_type2` refuses a type-5 record (leaves it
  registered for the right cleaner); `clear_type5` refuses a type-2
  record symmetrically.

### Notes
- Server-only addition; the existing Type-2 endpoints + GUI are
  unchanged (no client release needed yet). The v0.2.63 inject dialog
  will render type-5 rows in the Active-injections table via the
  cross-kind alias today — a future slice adds a Type-5 inject form.

## [0.2.65] - 2026-05-30

**SR-MPLS label-stack GUI field** — completes 0.2.64. The backend
understood `mpls_labels`; now the Stream Edit dialog exposes it so
users don't have to hand-edit JSON.

### What changed
- **`widgets/stream_dialog.py`** — Stream Edit's MPLS group gains a
  second row: **Label stack** (comma-separated SIDs, e.g.
  ``16000, 16001, 16002``). Accepts hex too (``0x10, 0x20``).
  - Placeholder + tooltip both call out that this is the SR-MPLS
    stack and that it overrides the single Label field above.
  - Save path parses the text into a list via the existing
    ``utils.mpls.extract_mpls_labels`` helper and writes
    ``mpls_labels: [..]`` to ``protocol_data.mpls``. Blank field
    → key omitted (pre-0.2.64 streams stay bit-identical).
  - Load path normalises a stored list back to comma-separated
    text so the user sees what they originally typed (not a
    Python list repr).
  - Garbage input is dropped silently — the legacy single-label
    field still goes through, so a typo doesn't brick the stream.
  - MPLS group cap raised 70 → 110 px to fit the second row
    (previously clipped).

### Verified — 9 new tests, full suite 227 passing
`tests/test_stream_dialog_mpls.py`:
- Field exists with helpful placeholder + SR-MPLS tooltip.
- Save: blank field omits `mpls_labels`; comma-separated text
  parses into a list; hex labels accepted; garbage input dropped
  without breaking the rest of the payload.
- Load: list normalises to comma-separated text; string passes
  through unchanged; empty stack → empty field.
- Round-trip: load → save preserves the stack.

(One unrelated flake in the stateful-TCP TLS-handshake test —
timing-sensitive, passes in isolation; not touched by this change.)

### Notes
- Client-only. Server side was already done in 0.2.64.

## [0.2.64] - 2026-05-30

**Enhancement #4 of 4 (final slice) — SR-MPLS label-stack support
(RFC 8660) in the scapy tx path.** The existing single-label MPLS
branch becomes a true label *stack* that can carry the SID list an SR
test needs.

### What changed
- **`utils/mpls.py`** (new): pure-function helper
  `build_mpls_stack(labels, tc=0, ttl=64)` that returns a chained
  scapy MPLS layer. Bottom-of-stack `s` bit is set on the last label
  only (scapy handles this automatically when MPLS layers are stacked
  via `/`). Validates every input — empty stack, 20-bit label range,
  3-bit TC, 8-bit TTL — and raises with a useful message rather than
  silently emitting a malformed frame. Companion
  `extract_mpls_labels(mpls_config)` reads both the new
  `mpls_labels: [..]` list AND the legacy scalar `mpls_label`, plus
  a comma-separated string ("100, 200, 300") for whatever the GUI
  field eventually looks like.
- **`utils/generic.py`** — the existing single-label MPLS branch
  routes through `build_mpls_stack` via `extract_mpls_labels`. Streams
  with `mpls_labels: [16, 200, 300]` get a proper 3-label stack with
  ethertype 0x8847; streams that only set the legacy `mpls_label`
  scalar produce the same bytes as before (back-compat verified).

### Wire format (pinned by 15 new tests)
- Single label → 4 bytes, S=1, ethertype 0x8847 on the Ether layer.
- N-label stack → 4N bytes; on-wire order matches the list order
  (top of stack first); only the bottom (last) label carries S=1.
- TC + TTL applied uniformly to every label (verified by parsing
  the raw bytes, not just the scapy object).
- L3 payload starts at exactly 14 + 4N bytes.
- 21-bit label, TC > 7, TTL > 255, negative inputs, and empty stack
  all raise `ValueError` at build time.
- `extract_mpls_labels` round-trips the new list, the legacy scalar,
  the comma-separated string, hex labels, falsy / missing config,
  and an invalid legacy string (returns `[]` rather than raising,
  so the caller treats it as "no MPLS").

### Verified
Full suite **218 passed** (15 new tests in `tests/test_mpls_stack.py`).

### Notes
- Back-compat: pre-0.2.64 streams (with `mpls_label` scalar) produce
  bit-identical bytes vs prior releases. New streams add
  `mpls_labels` to their `protocol_data.mpls` dict.
- The Stream Edit dialog doesn't yet expose `mpls_labels` as a GUI
  field — set it via the JSON `protocol_data` until that lands. A
  small text input ("Labels (comma-separated)") fits naturally next
  to the existing single-label field; that's the next iteration on
  this enhancement if you want it.

### Enhancement #4 complete
QinQ (0.2.60), BFD (0.2.61), EVPN Type-2 inject backend (0.2.62) and
GUI (0.2.63), and now SR-MPLS stacks (0.2.64). The protocol /
data-plane expansion menu from 0.2.56's plan is fully shipped — 4
enhancements, 9 releases (0.2.56–0.2.64), test count grew from 103
to **218** with every recent regression and every new feature
locked behind a regression test.

## [0.2.63] - 2026-05-30

**Enhancement #4 of 4 (slice 4) — EVPN Type-2 inject GUI.** GUI front
end for the v0.2.62 endpoints; no more curl required.

### What changed
- **`widgets/evpn_inject_dialog.py`** (new): the `EvpnInjectDialog` —
  one inject form (VXLAN iface, base MAC, count, optional base IP /
  remote VTEP / L3 iface) plus a live "Active injections" table with a
  per-row "Clear" button and a Refresh. Status line below the Inject
  button goes green on success, amber on partial failure, red on HTTP
  errors / connection problems. Dialog refreshes the active list on
  open and after every inject/clear.
- **`utils/devices_tab_vxlan.py`**: new **EVPN Inject** button on the
  Devices → VXLAN sub-tab action bar (right of Apply / Refresh).
  Opens the dialog with the server URL resolved via the existing
  `parent.get_server_url()` path and the VXLAN iface pre-filled from
  the currently-selected VXLAN row (when exactly one is selected).

### Wire format / behaviour
- Inject button → `POST /api/evpn/type2/inject` with the assembled
  body; success message echoes the count + ok-command count.
- Clear button per row → `POST /api/evpn/type2/clear` with that row's
  `inject_id`; success / partial-failure / error each map to a status
  colour.
- Refresh / on-open → `GET /api/evpn/type2/list`; populates the table.

Per-row Clear binds the row's own `inject_id` via an explicit lambda
default-arg, so the Python closure-capture bug (every button calling
clear on the LAST row) can't bite — regression-locked by a test that
clicks all rows and asserts the sequence.

### Verified — 14 new tests, full suite 203 passing
`tests/test_evpn_inject_dialog.py`:
- `build_inject_payload`: MAC-only minimal body / all optional fields /
  missing-required → None.
- `_populate_active`: every column carries the right value, missing
  optionals render "—", inject_id truncated with full ID in the
  tooltip, Clear cellWidget present.
- Clear closure: clicking each row's button calls `_clear_one` with
  *its own* `inject_id` (not the last loop value).
- `_on_inject`: posts the right URL + body; green status on full
  success; amber on partial failure; red + server error message on
  400; red + exception text on connection failure.
- `refresh_active`: populates the table from the server payload;
  swallows network errors without crashing.
- `_clear_one`: posts inject_id to /clear; amber on partial failure.

### Notes
- Client-only addition; no server change since the endpoints landed
  in 0.2.62. Old clients still scripted-only against the API; new
  clients can run inject + clear from the GUI.

### Next on this enhancement
0.2.64 (final slice of #4): SR-MPLS label-stack support in the
existing tx path — needs DPDK tx_worker C changes.

## [0.2.62] - 2026-05-30

**Enhancement #4 of 4 (slice 3) — EVPN Type-2 bulk injection.** Scale
test the existing EVPN/VXLAN BGP wiring by manufacturing N synthetic
MAC (+ optional IP) entries — let one chassis pretend to be a VTEP with
hundreds or thousands of endpoints.

### Mechanism
The EVPN address-family was already auto-enabled by
`configure_bgp_for_device` when a device has VXLAN config (advertises
all VNIs, sets route-targets, activates EVPN on the IPv4/IPv6
neighbours). What was missing was a way to *get entries into the MAC
table*. Real EVPN routers learn them from data-plane forwarding; the
traffic-generator path is to write them directly into the kernel:

  * ``bridge fdb append <mac> dev <vxlanN> master self static dst <vtep>``
    populates the VXLAN MAC table.
  * ``ip neigh add <ip> lladdr <mac> dev <iface> nud noarp`` adds the
    IP→MAC binding for MAC+IP Type-2 sub-routes.

FRR's zebra picks both up and BGP advertises them as Type-2 routes
under the existing l2vpn-evpn address-family.

### What changed
- **`utils/evpn_inject.py`** (new): pure helpers + a high-level entry
  point.
  - `mac_to_int` / `int_to_mac` / `generate_mac_range` /
    `generate_ip_range` — range arithmetic with strict validation.
  - `build_inject_commands(iface, entries, remote_vtep_ip, l3_iface)` /
    `build_clear_commands(...)` — pure command-list builders.
  - `inject_type2(...)` / `clear_type2(...)` — runs commands via an
    injectable ``run`` callable (subprocess.run by default; tests pass
    a fake). Registers each batch under a UUID so /clear can find it.
  - `list_active_injections()` — for the GUI table coming in 0.2.63.
- **`run_tgen_server.py`**: three new endpoints, all role-gated.
  - ``POST /api/evpn/type2/inject`` (operator)
  - ``POST /api/evpn/type2/clear`` (operator)
  - ``GET  /api/evpn/type2/list`` (viewer)
  Routes are thin wrappers around `utils.evpn_inject` — all logic +
  validation lives there.

### Verified — 18 new tests, full suite 189 passing
`tests/test_evpn_inject.py`:
- MAC parsing round-trips; rejects 7 different malformed shapes.
- MAC range crosses byte boundaries (`…00:fe` → `…01:00`).
- IP range crosses /24 boundaries.
- Command lists: MAC-only emits only `bridge fdb`; MAC+IP emits both;
  `remote_vtep_ip` appends `dst <vtep>`; `l3_iface` overrides the
  neigh interface (SVI vs VXLAN); clear order is neigh-then-FDB.
- `inject_type2` runs the right number of commands (2N for MAC+IP, N
  for MAC-only); partial failure surfaces per-command errors without
  aborting; subprocess exceptions are caught and recorded, not
  propagated; zero count rejected with ValueError.
- `clear_type2` drops the in-process record even when kernel commands
  fail (stale entry is common, must not leak the record); unknown
  inject_id returns a warning, not a 500.
- `list_active_injections` reflects inject/clear lifecycle.

### Notes
- Server-only addition. Inject endpoint is `require_role("operator")`,
  list is viewer-read. Bridge/`ip` commands run on the host (FRR
  containers are `--network=host`, established in 0.2.19), so the
  server process needs CAP_NET_ADMIN — already required by netgen for
  routing-table edits.
- GUI dialog (Devices → VXLAN sub-tab → "Bulk Inject Type-2") follows
  in 0.2.63; today the endpoints are scriptable via curl / ansible /
  any HTTP client.

### Next on this enhancement
0.2.63: GUI dialog for EVPN Type-2 inject; live "Active injections"
table with row-level Clear.
0.2.64: SR-MPLS label-stack support in the existing tx path (DPDK
tx_worker C changes).

## [0.2.61] - 2026-05-30

**Enhancement #4 of 4 (slice 2) — BFD emitter (RFC 5880 / 5881).**
Sixth L2-emulation protocol, joining LACP / LLDP / VRRP / IGMP / PIM
Hello.

### Wire format
`utils/l2_protocols.start_bfd` emits an `Ether / IP / UDP / Raw(BFD)`
frame at the configured cadence. BFD control payload is hand-packed to
the RFC 5880 §4.1 layout (scapy carries no stable BFD layer across
versions), 24 bytes, no auth:

  * Byte 0: Version (3) | Diag (5 bits)
  * Byte 1: State (top 2 bits) | Flags (bottom 6, all 0 — no Poll, no
    auth, no demand)
  * Byte 2: Detect Multiplier · Byte 3: Length (24)
  * Bytes 4-7 / 8-11: My / Your Discriminator (big-endian)
  * Bytes 12-23: Desired Min TX, Required Min RX, Required Min Echo
    RX intervals (µs, big-endian)

L3/L4 envelope: source UDP port 49152, dst port **3784** (single-hop;
override to 4784 multi-hop or 3785 echo), **IP TTL=255** as RFC 5881 §5
mandates so the receiver can verify the packet originated on the
directly-connected link. Inline 802.1Q and 802.1ad QinQ tagging
(0.2.41 / 0.2.60) are supported transparently.

State machine is intentionally not modelled — emitter sends a fixed
state at a fixed cadence, which is enough to: keep a peer's session Up
by asserting our liveness, or deliberately tear a session down by
sending State=Down.

### What changed
- **`utils/l2_protocols.py`** — new `start_bfd` factory. Defaults to
  State=Up, detect_mult=3, 1 Hz, dst_udp_port=3784, my_discriminator
  0x11111111. Pre-computes the 24-byte payload once and reuses it
  across every frame (no per-tick struct.pack cost).
- **`server/l2_routes.py`** — added `"bfd"` to `_PROTOCOL_FACTORIES`
  with the full kwarg allow-list (state, detect_mult, my/your
  discriminator, all three intervals, dst_udp_port, send interval,
  duration, + the inherited VLAN keys).
- **`widgets/l2_emulation_tab.py`** — `"BFD — Bidirectional Forwarding
  Detection (RFC 5880)"` added to the protocol picker. New BFD
  parameter panel with: state combo (Up/Down/Init/AdminDown), src/dst
  IP+MAC, My/Your Discriminator (hex or decimal), detect mult, desired
  min TX µs, required min RX µs, dst UDP port (3784 default), send
  interval (sub-second supported). Dispatch in `_on_accept` parses
  discriminators, rejects zero My Discriminator (RFC 5880 §6.8.1).
  Emerald protocol-badge colour `#059669` (distinct from running/
  stopped greens).

### Verified — 18 new tests, full suite 171 passing
`tests/test_bfd_l2.py`:
- Factory export + REST allow-list contains `start_bfd` with every
  expected kwarg.
- Envelope: dst port 3784 single-hop / 4784 multi-hop / TTL=255.
- Payload bytes: 24 bytes, version=3, length=24; state encoded in
  byte-1 top 2 bits for all four states; default flags = 0; diag in
  byte-0 bottom 5 bits without leaking into version; detect_mult in
  byte 2; discriminators at offsets 4 / 8 big-endian; intervals at
  12 / 16 / 20 big-endian µs.
- 802.1Q single-tag and 802.1ad QinQ wrapping keep the 24-byte payload
  intact and the outer ethertype correct (0x8100 / 0x88a8).
- Dialog round-trips state / discriminators / intervals / port;
  rejects zero discriminator and malformed text.

### Next on this enhancement
0.2.62: EVPN type-2 (MAC/IP) generator (BGP-side; UPDATE messages
carrying EVPN NLRI); SR-MPLS label-stack support in the existing tx
path.

### Notes
- Server-only addition; the existing factories are unchanged.

## [0.2.60] - 2026-05-29

**Enhancement #4 of 4 (slice 1) — protocol expansion: QinQ (802.1ad)
double-tag** for the L2 / Multicast Emulation tab.

### Wire format
`_l2_hdr` gains a second tag pair (`outer_vlan_id` / `outer_vlan_pcp`):

* **Untagged** (no VLAN ids): unchanged — bare `Ether(type=ethertype)`.
* **Single 802.1Q** (only `vlan_id`): unchanged — `Ether(type=0x8100) /
  Dot1Q(vlan, type=ethertype)`.
* **QinQ / 802.1ad** (both `outer_vlan_id` AND `vlan_id`): emits
  `Ether(type=0x88a8) / Dot1Q(outer, type=0x8100) / Dot1Q(inner,
  type=ethertype)`. Outer is the S-VLAN (service-provider), inner is the
  C-VLAN (customer); the protocol's original ethertype rides on the
  inner Dot1Q so upper layers still parse cleanly through the double
  tag.

Passing `outer_vlan_id` without `vlan_id` raises `ValueError` rather
than silently emitting a single-tagged frame.

### What changed
- **`utils/l2_protocols.py`** — `_l2_hdr` gets the new kwargs; all five
  factories (LACP, LLDP, VRRP, IGMP, PIM) forward them and stash them
  in the session's `config` dict.
- **`server/l2_routes.py`** — `_VLAN_KEYS` includes the two outer fields
  so the REST allow-list accepts them.
- **`widgets/l2_emulation_tab.py`** — Common-settings section gains
  **Outer VLAN ID** + **Outer VLAN PCP** spinners (default 0 = single
  tag). Dialog refuses outer-without-inner with a clear warning. L2
  sessions table's VLAN cell renders `"<outer> » <inner>"` for QinQ
  sessions, with a tooltip naming the S-/C-VLAN semantics; single-tag
  and untagged renders are unchanged.

### Verified — 13 new tests, full suite 153 passing
- `tests/test_qinq_l2_hdr.py` (6): outer TPID 0x88a8 + inner 0x8100 +
  original ethertype on inner; QinQ frame is +4 bytes over single-tag;
  outer PCP encodes in the TCI (verified by reparsing raw bytes);
  outer-without-inner raises ValueError; untagged & single-tag paths
  unchanged by the new kwargs (no regression).
- `tests/test_l2_emulation_qinq.py` (7): payload round-trips outer
  fields only when set; outer-without-inner refused by the dialog;
  sessions-table cell renders `"<outer> » <inner>"` with informative
  tooltip; single-tag and untagged renders unchanged.

### Notes
- Backward compatible end-to-end. Old clients ignore the new factory
  kwargs (they default to None); new clients with outer=0 produce the
  exact bytes the 0.2.41 single-tag tests pin.

### Next on this enhancement
0.2.61: BFD emitter (RFC 5880 packet format); EVPN type-2/3/5; SR-MPLS
/ SRv6 label-stack support.

## [0.2.59] - 2026-05-29

**Enhancement #3 of 4 (slice 2) — RFC 2544 latency capture + HTML
report.** Completes the §26.1 throughput test with §26.2 latency and a
formal printable deliverable.

### What changed
- **`run_tgen_server.py`** — `_rfc2544_run_step` accepts a
  `capture_latency` kwarg and, when set, embeds NLAT timestamps in the
  binary-search stream so the RX-side `LatencySampler` decodes them.
  `_rfc2544_thread` snapshots the sampler at the end of each frame
  size's search and stores it under the new `latency` key in the
  progress entry (min/avg/p50/p95/p99/max µs). Defensive: any
  snapshot failure leaves `latency=None` and the client renders "—".
- **`widgets/rfc2544_dialog.py`**:
  - New **"Capture latency (RFC 2544 §26.2)"** checkbox (default off,
    so the §26.1 throughput run stays bit-for-bit identical to prior
    releases for users that don't opt in).
  - Three new results-table columns: **Lat p50 / p95 / p99 (µs)**.
    Pre-0.2.59 servers (no `latency` field) render "—" — fully
    backward-compatible.
  - CSV export now includes the three latency columns.
  - New **"Export HTML Report"** button. Generates a self-contained,
    browser-printable HTML report (test parameters + full results +
    best-throughput summary). No PDF dep — the browser's
    Print → Save-as-PDF handles the PDF need. Pure-function
    `build_rfc2544_html_report(params, rows, server_url)` is exposed
    at module level so it's testable / scriptable.

### Verified
8 new tests in `tests/test_rfc2544_dialog.py`, full suite **140 passed**:
- Dialog construction (8-column results table, capture-latency
  defaults off, HTML button disabled until completion).
- Param payload carries `capture_latency`.
- Progress poll populates the new latency cells when a 0.2.59 server
  supplies them.
- Pre-0.2.59 server (no `latency` field) — cells render "—" cleanly,
  no crash, no `None` text.
- Mixed-percentile-availability — None percentiles render "—"
  per-cell (not whole-row).
- HTML report contains params + data rows + summary footer; is
  self-contained (no external links/scripts).
- Missing-latency HTML report renders "—" cells.
- Empty-results HTML report shows a clear "no results" message
  instead of a malformed table.

### Notes
- Server change is fully backward-compatible (additive `latency` field,
  opt-in via `capture_latency`). Old clients ignore the new field; new
  clients render "—" against old servers. The throughput numbers are
  unchanged when capture-latency is off.
- Pre-0.2.58 servers still won't return `p95_us` even with capture on —
  the client renders that specific cell as "—" and the rest still
  populate. Recommended: upgrade both ends.

### Next on the menu
0.2.60: enhancement #4 — protocol & data-plane expansion (QinQ
double-tag, BFD, EVPN type-2/3/5, SR-MPLS / SRv6).

## [0.2.58] - 2026-05-29

**Enhancement #3 of 4 (slice 1) — stats / latency / reports.** Adds
**p95** latency to the sampler and an **Export CSV** button on the stats
dock. The bigger pieces (RFC 2544 binary-search runner, PDF reports)
follow in 0.2.59.

### What changed
- **`utils/latency_sampler.py`** — `LatencyStats.snapshot()` now returns
  `p95_us` alongside the existing `p50_us` and `p99_us`. Most SLAs are
  stated in p95, and without it operators were eyeballing between p50
  and p99. Old-server-compatible (the empty-window snapshot also
  contains the key, set to `None`).
- **`traffic_client/statistics_section.py`** — Stream-Statistics table's
  Latency cell tooltip now includes a `p95` line (formatted only when
  the server actually returned one, so old servers don't render
  `None us`).
- **`traffic_client/statistics_section.py`** — new **Export CSV** button
  next to Clear Stats. Writes both stats tables to one CSV with a header
  block (timestamp + server addresses) and a `# Section: …` marker per
  table. Handles cell widgets (combos / checkboxes) via the obvious
  fallbacks; skips hidden (filtered) rows; writes a self-describing
  `# (no rows)` / `# (table not available)` marker rather than silently
  emitting nothing.

### Verified
11 new tests, full suite **132 passed**:
- `tests/test_latency_percentiles.py` (4): empty-snapshot key presence;
  min ≤ p50 ≤ p95 ≤ p99 ≤ max ordering on 1000 uniform samples; single-
  sample degenerate case; p95 strictly between p50 and p99 on skewed
  data (proves we didn't alias them).
- `tests/test_stats_csv_export.py` (7): `_dump_table_to_csv` for the
  common shapes (header, empty, None, missing item, hidden rows,
  combo/checkbox cell widgets) + an end-to-end test that mocks
  `QFileDialog`, runs `export_statistics_csv` against two real
  `QTableWidget`s on a real mixin, and parses the resulting CSV.

### Next on this enhancement
0.2.59: RFC 2544 dialog wired up (throughput / latency / loss / FLR with
binary-search rate convergence); per-frame-size results table; optional
PDF report.

### Notes
- Server change is additive (new `p95_us` field in the snapshot);
  pre-0.2.58 clients ignore it. Client tooltip falls back gracefully
  against a pre-0.2.58 server.

## [0.2.57] - 2026-05-29

**Enhancement #2 of 4 — Incremental in-place updates for the Streams
table.** Retires the entire bug class behind the v0.2.51 / .53 / .54 /
.55 regressions by removing the cause: the periodic full table rebuild.

### Insight
Every column on the Streams table is *configuration* (Interface, Name,
Enabled, Frame Type, sizes, L1/L2/L3/L4, VLAN, RX Port, Flow Tracking).
The ONLY thing that periodically changes from a stats poll is the col-0
**Status** icon. Yet the every-2 s stats poll was calling
`_do_update_stream_table()` — a full `setRowCount(0)` + re-`setItem` of
every cell of every row — purely to repaint that one icon. That
overkill interacting with selection / inline edits / display-derived
key lookups is what produced the four regressions.

### What changed
- **`traffic_client/server_section.py`**: new
  `_refresh_stream_status_in_place()`. Iterates current rows,
  identifies each by `stream_id` (stashed at `Qt.UserRole` on the Name
  cell), and updates ONLY the col-0 Status cell when the underlying
  status changes. Per-instance `_stream_status_pushed: {sid: color}`
  cache skips no-op updates. Signals blocked during the update so an
  itemChanged can't fire spuriously.
- **`traffic_client/statistics_section.py`**: `fetch_and_update_statistics`
  now calls `_refresh_stream_status_in_place()` instead of the full
  `_do_update_stream_table()`. The wall of `_populating_table` flag
  pokes around it is gone too.
- Structural changes (add / edit / remove / apply / start / stop)
  continue to call `_do_update_stream_table` from their own code paths —
  that's the right time for a full rebuild. The TG-prune path
  (`statistics_section.py:855`) also still rebuilds (a TG removal IS
  structural).

### What this kills
- **No more selection wipe on every poll.**
- **No more flicker every 2 s.**
- **The display-derived key reconstruction loop runs only on structural
  changes**, so the (port-cell-text vs. self.streams key) mismatch
  *cannot* fire on the periodic path. The four shipped regressions
  (Delete, Copy, Paste, Start All) would have been impossible.
- **Inline editing is unconditionally safe** during the periodic refresh
  because the in-place path touches only col 0 (not editable) and
  blocks signals.

### Verified
4 new tests in `tests/test_client_stream_ops.py` (13 tests total in that
file, 121 in the full suite):
- `status_in_place_no_rebuild_no_selection_loss` — 5 consecutive
  in-place refreshes leave row count, selection and the col-1/col-2
  item Python objects unchanged.
- `status_in_place_updates_changed_status_and_skips_unchanged` —
  flipping one stream's status repaints its col-0 cell; the other row's
  item is the same Python object (no churn); push-cache reflects the
  change.
- `status_in_place_does_not_close_open_editor` — editor on the Name cell
  survives 3 in-place refreshes that change col-0 on the same row.
- `status_in_place_handles_missing_or_pruned_streams` — row outliving
  its stream entry doesn't crash; structural rebuild not triggered.

Full suite: **121 passed**.

### Next on the menu
0.2.58+: stats / latency / reports — p50/p95/p99, RFC 2544 run,
CSV/JSON export.

### Notes
- Client-only. No server or wire-format change. The same in-place
  pattern can later be extended to the BGP/OSPF/IS-IS protocol tables
  if they grow periodic refreshes that don't actually need a rebuild;
  for now they remain rebuild-driven because their content (neighbor
  state) genuinely changes.

## [0.2.56] - 2026-05-29

**Enhancement #1 of 4 — Client GUI test coverage.** Lock the 4 recent
regression fixes against re-breaking, and lay the foundation for the
incremental-table-update refactor that comes next.

### What changed
- **`tests/conftest.py`**: added `qapp` (session-scoped, offscreen
  QApplication) and `client_stub` (factory that builds a minimal stub
  of the StreamControl + ServerSection mixins on a QWidget base, with
  every external handler no-op'd and modal QMessageBox calls silenced).
  Replaces the ~30-line ad-hoc harness I'd been re-typing into every
  inline repro.
- **`tests/test_qt_table_guard.py`** (5 tests): locks
  `utils.qt_table_guard.table_has_open_editor` returns True ONLY for a
  real cell editor — never for viewport / table focus or selection
  (which is the over-broad signal that broke stream delete in 0.2.50/51).
- **`tests/test_client_stream_ops.py`** (9 tests): one regression test
  per recent bug, plus a couple of supporting invariants:
  - Delete refreshes while the row is still selected (v0.2.51).
  - Selection is preserved across automatic refreshes.
  - Inline editor survives 4 consecutive stats polls (v0.2.49/52).
  - Focused-but-not-editing does **not** defer the refresh (v0.2.52).
  - Copy resolves bare-iface cell text via stream_id (v0.2.53).
  - Paste lands in the correct full key (no `" - Port: …"` ghost),
    primary path (v0.2.54).
  - Paste falls back to `server_interfaces` index when the TG widget
    has no text label.
  - Start All's `valid_ports` is built from stream_id, not bare iface —
    the bug that silently skipped every stream (v0.2.55).
  - Stop All's row index map is keyed by stream_id, not by
    (bare-iface, name) (v0.2.55).

### Why this comes first
The 4 recent bugs slipped through because there were **no client GUI
tests**. Each repro I built was correct but inline and disposable. Now
the same shape lives as proper tests — the next refactor (incremental
updates) can land without re-introducing what we just fixed.

### Verified
Full suite: **117 passed** (103 existing + 14 new). New tests run in
~0.25 s, so they stay in the regular suite.

### Next on the menu
0.2.57+: incremental table updates (retires the bug class) → stats /
latency / reports → protocol & data-plane expansion.

### Notes
- Test-only addition; no behavioural change. Client-only; no server or
  wire-format change.

## [0.2.55] - 2026-05-29

Proactive audit after the Copy/Paste bugs. Found **3 more** instances of
the same "UI cell text used as a model key" anti-pattern that the recent
bugs all share. Two of these were real user-facing breakage; one was a
silent visual miss.

### CRITICAL — `start_all_streams` silently skipped every stream
**`traffic_client/stream_logic.py`**: `valid_ports` was built from the
stream table's Interface cell (col 1), which holds the BARE iface
(``"eno8303"``), then compared to ``self.streams`` keys
(``"TG 0 - Port: eno8303"``). They never matched, so every port went
into ``unknown_ports`` and Start All did nothing — exactly the
``Skipped stale/unknown ports (not in current UI):
['TG 0 - Port: eno8303']`` log line the user saw. Fixed by collecting
displayed ``stream_id``s from the Name cell's ``Qt.UserRole`` and walking
``self.streams`` to find which full port keys contain any of them.

### HIGH — packet capture couldn't determine server URL
**`traffic_client/packet_capture.py`**: ``tg_id = parent_item.text(0)``
returned ``""`` because the TG node uses an itemWidget (status icon +
``"TG N"`` QLabel), not text(0). The server-URL lookup
(``"TG " + tg_id == "TG "``) silently failed and start-capture bailed.
Fixed with the same 3-tier resolution paste_stream_to_interface uses
(itemWidget label-with-text → server_interfaces by parent index →
legacy text(0)).

### MINOR (visual) — stop_all_streams couldn't update row icons
Same file: ``row_index_map`` was keyed ``(bare-iface, name)`` from cell
text but looked up with the full port_label, so the per-row red-icon
update on Stop All silently missed. (The stops themselves landed; only
the icon flip was lost.) Rekeyed by ``stream_id`` — the natural unique
id, and one already stashed at ``Qt.UserRole``.

### Other findings (no fix needed)
- `open_add_stream_dialog`, `server_section.py:1588/1803` use a naive
  `findChild(QLabel)` that picks the icon label first, but the icon
  label is pixmap-only (empty text), so the existing
  `server_interfaces`-index fallback rescues them. Working today,
  fragile, but not breakage.
- Devices tab's status poll remains safe — surgical in-place updates,
  never a key-rebuild.

### Verified (headless repro reproduces the user's log line)
With ``self.streams`` keyed ``"TG 0 - Port: eno8303"`` and table cells
showing ``"eno8303"``:
- buggy ``valid_ports = {'eno8303'}`` → ``'TG 0 - Port: eno8303'``
  flagged unknown → Start All would skip everything (matches the user's
  log exactly);
- fixed ``valid_ports = {'TG 0 - Port: eno8303'}`` → no skip.
Full suite **103 passed**.

### Theme
This is the 4th release in a row fixing the same anti-pattern (UI cells
display human-readable text but the model is keyed by a canonical string
built elsewhere). The proper structural fix is the
**incremental-table-update** refactor — rows carry the model id and
lookups happen by id, not by rebuilding display-derived keys. Still the
highest-leverage enhancement on the menu.

### Notes
- Client-only; no server or wire-format change.

## [0.2.54] - 2026-05-29

Fix stream **Paste** silently dropping streams (the log line gave it
away: ``[PASTE] 'str1' ->  - Port: eno8303`` — empty TG ID).

### The bug (`traffic_client/stream_control.py`)
`paste_stream_to_interface` resolved the destination TG with
``tg_id = parent_item.text(0).strip()``. But the TG node's column 0 is
no longer plain text — `update_server_tree` builds a custom
``itemWidget`` (status-icon QLabel + a separate "TG N" QLabel), so
``text(0)`` is ``""``. That produced ``tg_id = ""`` →
``full_port_name = " - Port: eno8303"`` → the paste appended into a
ghost key that the Streams table and the stats lookups never match, so
the pasted stream looked like it vanished.

### The fix
Three-tier TG-ID resolution that mirrors what `_do_update_stream_table`
already does for this same tree:
1. Iterate ``itemWidget(parent, 0).findChildren(QLabel)`` and pick the
   first label with **non-empty text** (the icon labels are pixmap-only,
   so this picks the actual "TG N" text label). Robust against the
   findChild-returns-icon-first ordering quirk that bites the naive
   approach.
2. Fall back to ``server_interfaces[indexOfTopLevelItem(parent)]`` and
   build ``"TG N"`` from the cached chassis record.
3. Last-resort fall back to legacy ``parent_item.text(0)``.
A user-facing warning fires only if all three miss; previously the
failure was silent.

### Verified (headless, prod-shaped server tree)
Primary path (icon-pixmap label + TG-text label): paste lands in
``"TG 0 - Port: eno8303"``, no ghost key, ``rx_port`` correct,
``str1`` auto-named.
Fallback path (icon-only widget, no text label): falls through to the
``server_interfaces`` index and still pastes correctly.
Full suite **103 passed**.

### Notes
- Pre-existing bug; this kind of "two pieces of UI built the same key
  from different inputs" is the same class as the v0.2.53 copy bug, and
  is what the incremental-table-update enhancement would retire for
  good. Client-only; no server / wire-format change.

## [0.2.53] - 2026-05-29

Fix **stream Copy** failing with *"Unable to resolve the selected streams
to copy"*.

### The bug (`traffic_client/stream_control.py`)
`copy_selected_stream` called `_get_stream_by_port_and_name(port, name)`
with `port` = the Interface cell's text (just the bare iface, e.g.
`"ens1f0"`). But `self.streams` is keyed by full port labels like
`"TG 0 - Port: ens1f0"`, so the helper's naive `self.streams.get(port)`
returned `[]` for every row → no streams resolved → the warning. Likely
exposed when the `"↳"` continuation-row marker was removed earlier
(before, the first row of each port held the full key, masking the bug).
`remove_selected_stream` already handles this with a 3-tier resolution
(stream_id → `find_port_key` → name); copy didn't.

### The fix
- Hardened `_get_stream_by_port_and_name` to fall back to `find_port_key`
  when the direct dict lookup misses — so it now tolerates either the
  full key or the bare iface name.
- Updated `copy_selected_stream` to prefer `stream_id` (stashed on the
  Name cell at `Qt.UserRole`) first, with the (port, name) helper as
  fallback — same robust 3-tier resolution `remove_selected_stream` uses.
  Survives renames and duplicate names across ports.

### Verified (headless repro)
With `self.streams` keyed `"TG 0 - Port: ens1f0"` and a row showing
`"ens1f0"`:
- before fix: naive lookup → `[]` (bug exposed),
- after fix: helper resolves to the correct stream by id,
- end-to-end: selecting 2 rows + Copy → `copied_streams` holds both,
  `stream_id` correctly stripped from each copy.
Full suite **103 passed**.

### Notes
- Pre-existing bug (not introduced by the recent styling / guard work),
  but a real one. Client-only. No server or wire-format change.

## [0.2.52] - 2026-05-29

Proactive audit after the stream-delete regression: fix the **same
edit-vs-refresh bug class** in the BGP / OSPF / IS-IS tables, and correct
an over-eager defer in the stream guard.

### Audit findings
- **Devices status table** — safe. Its 30 s poll does surgical in-place
  updates (Status text + ARP icons), never a full rebuild, so it can't
  clobber an edit or selection.
- **BGP / OSPF / IS-IS tables** — **same bug as streams.** All three are
  intentionally inline-editable (each has a `cellChanged` save handler;
  IS-IS explicitly makes ISIS Net / System ID / Hello Interval /
  Multiplier editable) AND are full-rebuilt by their periodic monitoring
  (`update_*_table`) with no editor guard. So an in-progress inline edit
  there got discarded when that protocol's monitor ticked.

### What changed
- **`utils/qt_table_guard.py`** (new): shared `table_has_open_editor(table)`
  predicate — true only when a real cell editor is open. Checks the view's
  `EditingState` and whether a genuine editor *child* holds focus, while
  explicitly **ignoring** focus on the table/viewport and any selection
  (those were the over-broad signals that broke delete).
- **`utils/devices_tab_bgp.py` / `devices_tab_ospf.py` / `devices_tab_isis.py`**:
  `update_*_table` now bails early if an editor is open in that table.
  Monitoring repaints on its next tick once the editor closes; explicit
  refreshes (no editor open) are unaffected.
- **`traffic_client/server_section.py`**: the stream guard now uses the
  shared helper too. This also fixes a latent over-defer introduced in
  0.2.50 — merely *focusing/clicking* a stream row (no editor) was pausing
  the live stats refresh; now only a real open editor defers it.

### Verified (headless)
Helper: focused-not-editing → False, editing → True, button-focus → False.
Streams: delete-while-selected refreshes; focused-not-editing refreshes
(no longer deferred); editing survives 4 polls. Full suite **103 passed**.

### Notes
- Client-only. No server or wire-format change.

## [0.2.51] - 2026-05-29

Fix **stream delete not refreshing** — a regression from the 0.2.49/0.2.50
inline-edit guard.

### The bug (`traffic_client/server_section.py`)
The interaction guard in `_do_update_stream_table` deferred a refresh
whenever the table had a **selection** (or focus). But `remove_selected_stream`
deletes the stream from the model and then calls `update_stream_table()`
**while the deleted row is still selected** — so the guard deferred the
rebuild and the row never disappeared, making Delete look broken. The same
over-broad guard also held up the post-Edit / post-Apply refresh.

### The fix
Narrowed the guard to defer **only** for in-progress input a rebuild would
actually destroy: an **open inline editor** (`state()==EditingState` or the
focused widget being a descendant of the table) or an **open combo
dropdown**. Selection and focus are no longer part of the condition —
selection is saved and restored across every rebuild anyway, so a refresh
can't lose it.

### Verified (headless repros)
- **Delete**: remove a stream while its row is selected → table refreshes,
  row count drops, remaining rows intact. (Was: stayed unchanged.)
- **Inline edit**: editor still survives 4 consecutive stats polls.
- **Selection**: a selected row stays selected across an automatic refresh.
- Full suite **103 passed**.

### Notes
- Client-only. No server or wire-format change.

## [0.2.50] - 2026-05-29

Harden the Streams inline-edit guard (follow-up to 0.2.49).

### What changed (`traffic_client/server_section.py`)
- Added a **focus-descendant** check to `_do_update_stream_table`'s
  interaction guard: if the app's focused widget is a child of the stream
  table, an inline cell editor is open, so the periodic refresh is
  deferred. This is the most reliable "is the user editing" signal.
- Reproduced headlessly that on PyQt5 5.15.11 + Python 3.14,
  `state() == EditingState` and `selectedRows()` can **both** report
  False while an editor is genuinely open — in which case the old guard
  (which relied only on those) would let a poll rebuild the table and
  close the editor. The focus-descendant signal catches that case.

### Verified
Headless reproduction: open an editor on the Name cell, fire 4 simulated
stats polls — the editor stays open and the row is intact every time;
the focus-descendant signal reads True throughout. Full suite **103
passed**.

### Notes
- Client-only. **The running client must include this fix** — a published
  wheel / client reinstall is required; updating the server alone does
  nothing for this (it's a GUI-side guard).

## [0.2.49] - 2026-05-29

Fix inline editing in the **Streams table** being interrupted by the
periodic stats refresh.

### The bug (`traffic_client/server_section.py`)
`_do_update_stream_table` has a guard that should defer a refresh while
the user is interacting (row selected / inline-editing a cell / combo
dropdown open). But the `return` that skips the rebuild was **nested
inside** the `if not self._pending_stream_refresh:` branch:

- 1st poll during an edit → flag is False → schedule a retry, set the
  flag, `return` (correctly deferred).
- 2nd+ poll during the *same* edit → flag is now True → the `if not …`
  block is skipped → control **falls through and rebuilds the table**,
  closing the open editor.

Because the stats poller fires every ~2 s and an edit takes longer, the
second poll always interrupted you mid-edit.

### The fix
The `return` now fires unconditionally whenever you're interacting; only
the *first* deferred pass schedules the single catch-up retry. The editor
stays open for the whole edit, and the table refreshes on the next poll
once you're done (which clears `_pending_stream_refresh`). This covers
both refresh paths — the debounced `update_stream_table` and the direct
`_do_update_stream_table()` call from the stats poller.

### Verified
Control-flow simulation: across 4 consecutive polls during a sustained
edit, zero rebuilds and exactly one scheduled retry; refresh resumes
after the edit ends. Full suite **103 passed**.

### Notes
- Client-only. Pre-existing debounce bug (not introduced by the recent
  styling work). No server or wire-format change.

## [0.2.48] - 2026-05-29

Finish the cross-tab table-consistency pass: the **L2 Emulation** table
now shows the row-number gutter like every other data table.

### What changed (`widgets/l2_emulation_tab.py`)
- Removed `verticalHeader().setVisible(False)`. The L2 table was the lone
  outlier hiding the row-number gutter; Devices, Streams and all the
  protocol sub-tables (BGP / OSPF / IS-IS / DHCP / VXLAN) show it. The
  table now matches them. Rows stay compact (24 px).

### Audit result (no other changes needed)
- **Config-tab data tables** (Devices, Streams, BGP/OSPF/IS-IS/DHCP/VXLAN,
  L2) — all now plain default Qt chrome + row-number gutter. Consistent.
- **Topology tab** — a graphics/diagram view, no table. N/A.
- **Statistics dock** (Interface + Stream stats) — intentionally left
  with its monospace 12 px font + taller rows for live-throughput
  readability (a distinct monitoring surface, per user direction).
- **Server tree** (left navigator) — a styled `QTreeWidget`, distinct nav
  element, unchanged.

### Verified
Headless render confirms the gutter is shown and the table is otherwise
unchanged. Full suite **103 passed**.

### Notes
- Client-only. No server or wire-format change.

## [0.2.47] - 2026-05-29

Make the **Streams table** visually identical to the Devices tab —
plain default Qt chrome.

### What changed (`traffic_client/stream_control.py`)
- Removed the Streams table's custom stylesheet entirely (the slate/
  coloured `QHeaderView` header, alternating row colours, brightened
  `#2563eb` blue selection, 13 px body font) plus the explicit header
  sizing (`setDefaultSectionSize`, `setFixedHeight(22)`). The table now
  renders with the same plain default Qt chrome as the Devices / BGP /
  OSPF / IS-IS tables. Only `setHighlightSections(False)` is kept
  (cosmetic, harmless).
- Functional behaviour is untouched: inline-edit triggers, row selection
  / extended multi-select, `ResizeToContents`, status-dot icons, and the
  per-column header tooltips all stay.

### Why
After 0.2.46 the Streams table still carried its old
"bumped-for-visibility" styling, so it looked different from the Devices
tab. The user asked for them to match; the cleanest way is to drop the
lone custom stylesheet so every data table in the app is consistent. The
running/total status chip added in 0.2.46 lives in the action bar (not
the table) and is unaffected.

### Verified
Byte-compiled; rendered the real Streams section next to a plain default
Qt table (Devices-tab equivalent) — header, rows, and selection now
match. Full suite **103 passed**.

### Notes
- Client-only. No server or wire-format change.

## [0.2.46] - 2026-05-29

Bring the L2-emulation-tab polish to the **Streams tab**: a live
running/total status chip and a cleaner table header.

### What changed
- **`traffic_client/stream_control.py`**: added a status chip
  (`● N running · M total`) to the right end of the Streams action bar —
  same widget/idiom as the L2 emulation tab (green when streams are
  running, slate when idle). New `_set_stream_count_chip(running, total)`
  helper. Also **cleaned up the table header**: dropped the heavy
  `#e5e7eb` fill + 1 px cell borders + letter-spacing for a softer
  `#f1f5f9` header with a single 2 px bottom rule (matches the L2 tab's
  header). The bumped-for-visibility body styling (alternating rows,
  blue selection) is unchanged.
- **`traffic_client/server_section.py`**: `_do_update_stream_table` now
  counts running streams while it builds the table and refreshes the
  chip in its `finally` block (counters initialised before the `try` so
  an early-exit can't `NameError`). Because start / stop / start-all /
  stop-all all call `update_stream_table()`, the chip updates on every
  state transition.

### Why
The user asked for the Streams tab to pick up the L2 tab's "more
professional" treatment. The Streams tab already shared the compact
action-bar + tight-margins pattern (it's where that pattern originated),
so this adds the missing piece — an at-a-glance running/total indicator —
and harmonises the header.

### Verified
Byte-compiled both files; headless chip-logic assertions; rendered the
real Streams section (stubbed handlers) showing the chip + new header.
Full suite **103 passed**.

### Notes
- Client-only. No server or wire-format change.

## [0.2.45] - 2026-05-29

Re-style the L2 / Multicast Emulation tab to match the **Devices tab**
and reclaim still more vertical space for the session table.

### What changed (`widgets/l2_emulation_tab.py`)
- **Dropped the header banner** entirely — the top-level tab is already
  labelled, so the banner was redundant chrome (same reasoning the
  Devices tab used to drop its "Device List" label).
- **Controls moved into a Devices-tab-style action bar**: a light-grey
  `QFrame` strip (`#f3f4f6`, bottom border only) that sits flush on top
  of the table so the two read as one panel. Buttons are fixed-height
  (24 px) with the shared neutral/danger styling; the running/total
  status chip moved to the right end of this bar.
- **Table now uses plain default Qt chrome** like the Devices / BGP /
  OSPF / IS-IS tables (removed the custom alternating-rows / no-gridline
  / rounded-border stylesheet). Row height trimmed to 24 px. The rich
  cell content — status pill, per-protocol colour badge, VLAN column,
  human-readable counters — is unchanged (it's applied per-item, not via
  the table stylesheet).
- **Removed the footer hint row**; the CAP_NET_RAW / root note is now a
  tooltip on the table.
- Outer margins `8/6/8/4 → 2/2/2/2`, spacing `5 → 0`.

### Why
Follow-up to 0.2.43/0.2.44. The banner + footer + padded toolbar were
eating a disproportionate share of the tab; folding everything into a
single action bar (Devices-tab pattern) leaves nearly the whole tab for
session rows and keeps the look consistent across tabs.

### Verified
Headless render of empty + populated states; smoke assertions pass; full
suite **103 passed**.

### Notes
- Client-only (`widgets/l2_emulation_tab.py`). No functional change.

## [0.2.44] - 2026-05-29

Make the L2 / Multicast Emulation tab **more compact** — the chrome was
eating vertical space and squeezing the session table.

### What changed (`widgets/l2_emulation_tab.py`)
- **Header collapsed to a single row**: the title and the protocol list
  (`LACP · LLDP · …`) now share one line instead of stacking, with the
  status chip beside them. Tighter banner padding.
- **Slimmer toolbar buttons** (reduced vertical padding) and a slimmer
  status chip.
- **Table row height 30 → 26 px** so more sessions fit on screen.
- **Footer condensed to one short line**, with the full
  CAP_NET_RAW/root explanation moved to its tooltip.
- Reduced outer margins / inter-widget spacing.

### Why
Follow-up to 0.2.43: on a normal window the header + toolbar + footer
were taking a disproportionate share of the tab, leaving the table a thin
strip. This reclaims that space for session rows.

### Verified
Headless render of empty + populated states; smoke assertions still pass.
Full suite green (one timing-sensitive stateful-TCP test flaked then
passed in isolation — unrelated to this client-only change).

### Notes
- Client-only (`widgets/l2_emulation_tab.py`). No functional change.

## [0.2.43] - 2026-05-29

Visual redesign of the **L2 / Multicast Emulation** tab for better
readability and a more professional look.

### What changed (`widgets/l2_emulation_tab.py`)
- **Header banner**: title + one-line protocol summary, with a live
  status chip on the right (`● N running · M total`) that turns green
  when something is emitting and amber when the server can't serve
  `/api/l2/*`.
- **Restyled toolbar**: primary-green Start, flat neutral
  Stop-selected / Refresh, outlined-red Stop-all, pointer cursors, and a
  right-aligned status/notice line (errors now render in amber instead
  of plain grey).
- **Richer session table**:
  - New **VLAN** column (shows the inline 802.1Q tag + PCP, or
    `untagged`) and a dedicated **Failed** column for `frames_failed`
    (red when non-zero).
  - **Status pill** (`● Running` / `● Stopped`) and a per-protocol
    **colour-coded badge** (LACP/LLDP/VRRP/IGMP/PIM).
  - Human-readable formatting: thousands-separated frame counts,
    `KB/MB/GB` byte sizes, and compact `2h 5m 9s` uptime (was raw bytes
    and `123.4`-second floats).
  - Session ID is monospaced + truncated with the full UUID on hover;
    columns reordered glance-first (Status → … → Session ID → Last
    Error), alternating row colours, styled header, no gridlines.
- **Config dialog polish**: dialog header + subtitle, common fields
  grouped under a **Common settings** box (distinct from the
  protocol-specific section), and a green **Start** button.

### Why
The old tab was a bare toolbar over a plain table that printed raw byte
counts and second-floats and didn't surface the VLAN tag or failed-frame
count at all. This makes session state readable at a glance.

### Verified
Headless render + assertions on formatting, columns, VLAN display,
session-id stashing (for Stop-selected), and dialog payload round-trip.
Full suite **103 passed**.

### Notes
- Client-only (`widgets/l2_emulation_tab.py`). No server or wire-format
  change; the data shown all comes from the existing
  `/api/l2/sessions` snapshot.

## [0.2.42] - 2026-05-29

Fix a regression in the lazy `FRRDockerManager` proxy that prevented
attributes from being **set** on the `frr_manager` singleton.

### What changed
- **`utils/frr_docker.py`**: removed `__slots__ = ()` from
  `_LazyFRRManager`. Without a `__dict__`, any attempt to *set* an
  attribute on the proxy (`frr_manager.foo = ...`) — or to patch one via
  `unittest.mock.patch.object(frr_manager, ...)` — raised
  `AttributeError: '_LazyFRRManager' object … has no __dict__ for setting
  new attributes`. The proxy now has a normal `__dict__`: set/patched
  attributes live there and shadow `__getattr__`, while *unset* attributes
  still fall through to the real manager (and still defer the Docker
  connect until first real use, preserving import-without-Docker).

### Why
The `__slots__` was added in 0.2.29 as a micro-optimisation but broke
`patch.object(frr_manager, "vrf_name_for_device", …)` in the VRF-wiring
tests and would have broken any production code that assigns onto the
singleton.

### Verified
Full suite **103 passed** (`tests/test_vrf_wiring.py` 6/6 green again).

### Notes
- Server-side only (utils/frr_docker.py). No client or wire-format change.

## [0.2.41] - 2026-05-28

Add **inline 802.1Q (Dot1Q) VLAN tagging** to L2 emulation — Spirent-style
encapsulation, so you can send tagged LACP/LLDP/VRRP/IGMP/PIM frames
without pre-creating a `vlanN` subinterface on the host.

### What changed
- **`utils/l2_protocols.py`**: new `_l2_hdr(src, dst, ethertype,
  vlan_id, vlan_pcp)` helper builds the Ethernet header — plain
  `Ether(type=ethertype)` when untagged, or
  `Ether(type=0x8100)/Dot1Q(vlan, prio, type=ethertype)` when tagged,
  preserving the protocol's original ethertype on the tag. All five
  builders now route their header through it and accept `vlan_id` /
  `vlan_pcp` (stored in the session config too).
- **`server/l2_routes.py`**: `vlan_id` + `vlan_pcp` added to every
  protocol's allow-list so the fields reach the factory.
- **`widgets/l2_emulation_tab.py`**: the Start-session dialog gains
  **VLAN ID** (0 = untagged) and **VLAN PCP** (0–7) fields in the common
  section — they apply to whichever protocol you pick. `vlan_id` is only
  sent when > 0, so untagged behaviour is unchanged.

### Verified (wire format)
For LACP/LLDP/IPv4/IPv6 ethertypes: untagged frames carry no Dot1Q;
tagged frames are exactly **+4 bytes** with outer ethertype `0x8100`,
correct TCI (e.g. PCP 3 + VID 100 → `0x6064`), the inner ethertype
preserved, and the upper-protocol payload byte-identical to the untagged
frame. Dialog emits `vlan_id`/`vlan_pcp` only when VLAN ID > 0.

### Why
Closes the main usability gap vs Spirent's encapsulation-stack model:
you tag inline in the dialog instead of having to `ip link add … type
vlan` a subinterface first.

### Notes
- Server + client (utils/l2_protocols.py, server/l2_routes.py,
  widgets/l2_emulation_tab.py).
- Wheel ships as `ostg_trafficgen-0.2.41-py3-none-any.whl`.

## [0.2.40] - 2026-05-28

Two more L2-emulation protocol-correctness fixes, plus a full builder
sweep.

### VRRP — source from the virtual router MAC
VRRP advertisements defaulted to an arbitrary source MAC
(`00:11:22:33:44:03`). A real master sources them FROM the RFC 5798
virtual router MAC so downstream switches learn it on the master's port.
`start_vrrp` now derives `00:00:5e:00:01:{vrid}` (IPv4) /
`00:00:5e:00:02:{vrid}` (IPv6) when `src_mac` is blank, and the dialog's
Source-MAC field is now blank-by-default with an "auto" placeholder
(enter a value only to override).

### IGMPv2 — Leave goes to all-routers
An IGMPv2 Leave (`type_code=0x17`) was sent to the group address; RFC
2236 §3 requires Leaves go to **224.0.0.2** (all-routers). `start_igmp`
now routes Leave to 224.0.0.2 (`01:00:5e:00:00:02`) with `gaddr` still
the group being left; Membership Reports (0x16) and group-specific
Queries (0x11) continue to target the group.

### Builder sweep — no further issues
Introspected the installed scapy against every L2 builder: LACP
(`actor_*` fields), LLDP (Chassis/Port/TTL/SysName/SysDesc TLV order +
field names), PIM Hello (`holdtime`/`dr_priority`/`generation_id`),
VRRP/VRRPv3, IGMPv3 — all field names and constants correct. No further
bugs found.

### Verified
VRRP virtual-MAC derivation across VRIDs + IPv6 + explicit override;
IGMP frames for Report/Leave/Query show the correct L2+L3 destinations.

### Notes
- Server + client (utils/l2_protocols.py, widgets/l2_emulation_tab.py).
- Wheel ships as `ostg_trafficgen-0.2.40-py3-none-any.whl`.

## [0.2.39] - 2026-05-28

Fix: L2 emulation always defaulted its interface to **loopback** — which
is useless, since LACP/LLDP/VRRP/IGMP/PIM frames must egress a real NIC
toward the switch.

### Cause
`L2EmulationTab._guess_default_iface()` returned `ifaces[0]` — the first
entry of the server's `/api/interfaces` list. That list returns **`lo`
first** (confirmed on svl-hp-ai-srv02:
`['lo', 'ens14f0', …, 'ens6f1np1', …]`), so the Start-session dialog
pre-filled `lo` every time.

### Fix
- Added `_skip_as_default_iface()` — skips `lo`/`lo0`/`loopback` and
  obvious virtual/non-egress devices (`vrf-`, `docker`, `br-`, `veth`,
  `virbr`, `tap`, `tun`).
- `_guess_default_iface()` now returns the **first real egress NIC** from
  the first online server's cached list (e.g. `ens14f0` instead of `lo`).
  Still editable in the dialog; falls back to `eth0` if none cached.

### Verified
Against srv02's actual interface order, the picker now skips `lo` (and
docker/vrf/veth/virbr) and selects `ens14f0` — the first physical NIC.

### Notes
- Client-only (widgets/l2_emulation_tab.py).
- Wheel ships as `ostg_trafficgen-0.2.39-py3-none-any.whl`.

## [0.2.38] - 2026-05-28

Fix an IGMPv3 packet bug in L2 emulation: the report frame had a
mismatched Ethernet vs IP destination.

### Bug
`utils/l2_protocols.py::start_igmp` derived the Ethernet destination
MAC from the **group** address for *both* IGMP versions. But an IGMPv3
Membership Report's IP destination is **224.0.0.22**, not the group —
so v3 reports went out addressed at L2 to the group's MAC (e.g.
`01:00:5e:01:01:01` for 239.1.1.1) while the IP header said 224.0.0.22.
That L2/L3 mismatch makes IGMP-snooping switches process the report on
the wrong multicast MAC (or drop it), so v3 membership wasn't being
learned correctly. (IGMPv2 was fine — there both L2 and L3 target the
group.)

### Fix
- Added `_ipv4_mcast_mac()` — the RFC 1112 §6.4 mapping
  (`01:00:5e` + low 23 bits of the IPv4 group), including the high-bit
  mask (`239.255.255.250 → 01:00:5e:7f:ff:fa`).
- IGMPv3 now sets the Ethernet dst to `01:00:5e:00:00:16` (the MAC for
  224.0.0.22), matching its IP dst. IGMPv2 keeps the group-derived MAC.
- Both versions now derive the L2 dst through the shared helper so they
  can't drift apart again.

### Verified
- MAC-mapping unit check across 224.0.0.22, group, all-routers, and the
  0x7f-mask edge case.
- Built a real IGMPv3 frame: `Ether dst = 01:00:5e:00:00:16`,
  `IP dst = 224.0.0.22`, TTL 1 — L2/L3 now match.

### Notes
- Server-side fix (utils/l2_protocols.py).
- Wheel ships as `ostg_trafficgen-0.2.38-py3-none-any.whl`.

## [0.2.37] - 2026-05-28

Cosmetic fix: the SSH manual-upgrade log no longer prints a scary
(but harmless) BrokenPipeError on a fully successful upgrade.

### Symptom
A successful SSH upgrade (0.2.4 → 0.2.32, service restarted,
`/api/health` OK, `exit rc=0`) still showed:
```
ERROR: Pipe to stdout was broken
Exception ignored on flushing sys.stdout:
BrokenPipeError: [Errno 32] Broken pipe
```

### Cause
The post-install verification ran `pip3 show ostg-trafficgen | head -2`.
`head` reads two lines then closes the pipe, so `pip` receives SIGPIPE
while still writing — pip dutifully prints the broken-pipe error. The
upgrade itself was unaffected (the Name/Version lines were captured,
`head` exits 0, the `&&` chain continued, rc=0).

### Fix
Replaced `| head -2` with `2>/dev/null | grep -E '^(Name|Version):'` in
the SSH upgrade command (`widgets/install_server_dialog.py`). `grep`
consumes pip's entire stdout, so pip never gets SIGPIPE — and it still
shows exactly the Name + Version lines. Verified the `shlex.quote`
wrapping keeps the embedded single-quotes intact on the non-root
`sudo sh -c` path. (The server-side HTTP upgrade builds its pip command
as a subprocess list — no shell pipe — so it never had this issue.)

### Notes
- Client-only (widgets/install_server_dialog.py); purely log-noise.
- Wheel ships as `ostg_trafficgen-0.2.37-py3-none-any.whl`.

## [0.2.36] - 2026-05-28

Docs: bring the install/upgrade guidance up to date with the
v0.2.34/v0.2.35 upgrade improvements.

### In-app guide (Help → Install Guide)
`_INSTALL_GUIDE_HTML` gains a new **§1a "Two ways to upgrade a running
server"** that documents:
- **Upload && Upgrade** (HTTP) vs **Upgrade via SSH (manual)** — when to
  use each.
- The HTTP path's **auto-fallback to SSH** on a missing/erroring endpoint
  (404/5xx), and that the manual button is the direct path for **old
  servers** without `/api/admin/upgrade_wheel`.
- The restart trying `netgen-server` then the legacy `ostg-server` unit.
- The equivalent manual `scp + pip + restart` one-liner, plus a note that
  0.2.28+ self-heals the FRR/DHCP assets on restart.
The Upgrade-tab row in §1 was updated to mention both buttons.

### Repo INSTALL.md
"Updating an existing install" now leads with a **"From the GUI
(operators)"** subsection (the two buttons + manual one-liner + self-heal
note), with the existing repo-script flow kept under "From the repo
scripts (developers)".

### Notes
- Docs-only (widgets/stream_dialog.py guide HTML, INSTALL.md).
- Verified: guide HTML parses, new sections present, dialog opens.
- Wheel ships as `ostg_trafficgen-0.2.36-py3-none-any.whl`.

## [0.2.35] - 2026-05-28

Add an explicit **"Upgrade via SSH (manual)"** button to the
Install/Upgrade Server dialog's Upgrade tab.

### Why
Until now the SSH upgrade path was only reachable as an *automatic*
fallback after the HTTP attempt failed (v0.2.34 made 404 trigger it).
For an old server you KNOW lacks `/api/admin/upgrade_wheel`, waiting for
the HTTP round-trip to 404 is pointless — and some operators just prefer
the direct pip-over-SSH path. There was no way to invoke it directly.

### Change (widgets/install_server_dialog.py)
- The Upgrade tab now has **two** buttons side by side:
    * **Upload && Upgrade** — HTTP path (auto-falls back to SSH on
      404/5xx/network when the SSH option is enabled).
    * **Upgrade via SSH (manual)** — skips HTTP entirely: sftp the wheel
      → `pip install --upgrade --force-reinstall --no-deps` → restart
      the service (`netgen-server` or legacy `ostg-server`). Uses the SSH
      credentials entered on the tab.
- New `_start_ssh_upgrade_manual()` validates the wheel + server, then
  calls the shared SSH worker directly (`_try_ssh_fallback(manual=True)`,
  which logs "Manual SSH upgrade" rather than "HTTP endpoint failed").
- Both buttons share a `_set_upgrade_busy()` helper so a run can't be
  double-started from the other button.

### Notes
- Client-only (widgets/install_server_dialog.py).
- Verified headless: dialog builds with both buttons, manual handler
  wired, busy-state toggles both.
- Wheel ships as `ostg_trafficgen-0.2.35-py3-none-any.whl`.

## [0.2.34] - 2026-05-28

Fix the in-GUI upgrade so it works against **old servers** that predate
the `/api/admin/upgrade_wheel` endpoint.

### Problem
On a server without the HTTP upgrade endpoint, the upload returns
**HTTP 404**. The dialog's worker only offered the SSH fallback on
network errors or **5xx**; it lumped 404 in with "client/input errors"
(400/409/413) and just failed. So the GUI couldn't upgrade exactly the
servers that most need upgrading.

### Fixes (widgets/install_server_dialog.py)
1. **404 / 501 → SSH fallback.** These specifically mean "endpoint
   missing" (server predates the feature), which is the canonical
   SSH-recoverable case. The dialog now emits `http_endpoint_broken` and
   offers the SSH upgrade path (sftp wheel → `pip install` →
   service restart). Genuine 4xx input errors (400/409/413) still don't
   offer SSH — those need the operator to fix the input.
2. **Legacy service name.** The SSH upgrade hardcoded
   `systemctl restart netgen-server`, but old servers often still run
   the `ostg-server` unit. The restart now does
   `systemctl restart netgen-server || systemctl restart ostg-server`,
   so it works on both.

### How to upgrade an old server (no `/api/admin/upgrade_wheel`)
- **GUI**: Install/Upgrade Server → it now auto-detects the missing
  endpoint (404) and offers SSH — enter SSH creds and go.
- **Manual** (equivalent one-liner):
  ```
  scp ostg_trafficgen-0.2.34-py3-none-any.whl root@<server>:/tmp/
  ssh root@<server> 'pip3 install --upgrade --force-reinstall --no-deps \
      /tmp/ostg_trafficgen-0.2.34-py3-none-any.whl && \
      (systemctl restart netgen-server || systemctl restart ostg-server)'
  ```
  Once on 0.2.34+, the startup self-heal deploys the FRR/DHCP assets and
  rebuilds the image, and future upgrades can use the HTTP path.

### Notes
- Client-only (widgets/install_server_dialog.py).
- Wheel ships as `ostg_trafficgen-0.2.34-py3-none-any.whl`.

## [0.2.33] - 2026-05-28

Surface the v0.2.32 service-health verdict in the **Add TGEN Chassis**
window's "Recent connections" table, so the operator sees a chassis's
health (not just reachability) right where they pick which TGen to
connect to.

### Changes
- `ReachabilityWorker` now also probes `/api/admin/health` (after the
  `/api/health` reachability check) and carries the `health` verdict +
  `issues` in its result signal.
- New **Health** column (col 5) in the table, and the status LED (col 0)
  is now 3-state:
    * **✓ green** — reachable + healthy (or a pre-0.2.32 server with no
      verdict — never false-amber)
    * **▲ amber** — reachable but degraded; the Health cell shows
      "Degraded" and the tooltip lists the reasons (e.g. "DPDK installed
      but no hugepages allocated")
    * **✗ red** — unreachable; Health shows "Offline"
    * **—** — reachable but no verdict (server < 0.2.32 or
      `/api/admin/health` auth-gated)
- Probe runs on dialog open (auto) and on "Test all", so the table
  reflects live health each time you open Add TGEN Chassis.

### Notes
- Client-only (widgets/add_tgen_dialog.py); pairs with the v0.2.32
  server verdict.
- Verified headless: 8-column table, all four states render correctly,
  no false amber against a pre-verdict server.
- Wheel ships as `ostg_trafficgen-0.2.33-py3-none-any.whl`.

## [0.2.32] - 2026-05-28

Enhancement: the per-TGen status LED now reflects **service health**,
not just reachability. Previously the dot was 2-state — green
("answers HTTP") / red ("doesn't"). A chassis whose Flask was up but
whose data plane was broken (the real "stream starts and stops"
incident: DPDK installed but hugepages=0) still showed green. Now
there's a third state.

### Server — health verdict on /api/admin/health
The endpoint now returns `health` ("healthy"|"degraded"), `degraded`
(bool), and `issues` (list of human-readable strings). Verdict rules
are deliberately conservative to avoid false-positive amber:
- `install/build in progress` → degraded (transient/busy)
- DPDK **installed** AND `hugepages.total == 0` → degraded
- DPDK **installed** AND `tx_worker` binary missing → degraded
- Kernel/scapy deployments (no DPDK) and fully-healthy DPDK hosts stay
  healthy. `vfio` is intentionally NOT a trigger — `vfio_pci` is often
  a builtin the module probe can't see, which would flag spurious amber.

### Client — 3-state LED
- New `poll_server_health()` (in `server_section.py`) hits
  `/api/admin/health` for each ONLINE server every **30 s**, in a
  background thread (keepalive-tracked, non-blocking). The endpoint is
  heavier than the reachability probe (it shells out to pkg-config /
  lsmod / proc), hence the modest cadence.
- New 3-state renderer `_update_server_led()`:
    * **red** — unreachable (offline)
    * **amber** (`yellow_dot.png`) — reachable but `degraded`; the
      tooltip lists the specific issues (e.g. "Degraded — DPDK installed
      but no hugepages allocated")
    * **green** — reachable and healthy
- Reachability (green↔red) is still owned by the interface-fetch /
  retry probes; the health poll only refines green↔amber for reachable
  servers, so the two never disagree. `update_server_status_icon` now
  delegates to `_update_server_led` so a learned health verdict
  survives reachability flips and tree rebuilds.
- Backward compatible: an older server that doesn't return the verdict
  fields resolves to green (no false amber).

### Notes
- Server + client change (run_tgen_server.py, server_section.py, main.py).
- Verified headless against svl-hp-ai-srv02 (no crash; healthy server →
  green) and unit-tested the verdict matrix.
- Wheel ships as `ostg_trafficgen-0.2.32-py3-none-any.whl`.

## [0.2.31] - 2026-05-28

Fix the gateway/IP ARP-status colors not updating on a passive view
(e.g. a gateway whose ARP failed stayed in normal font instead of
turning orange).

### Diagnosis (server was right; client wasn't refreshing)
Traced end-to-end on svl-hp-ai-srv02: `/api/device/arp/<id>` correctly
reported `arp_gateway_resolved: false` (VRF-aware ping, 100% loss) and
the DB stored `0`. The client's coloring logic
(`set_status_icon_with_individual_ips`) was also correct. The gap: the
chain that applies it — `status_timer → poll_device_status →
_refresh_device_table_from_database → set_status_icon_with_individual_ips`
— never ran, because `status_timer.start()` was commented out
("DISABLED to prevent QThread crashes"). So colors only updated on a
manual refresh / right after an operation, never periodically. A
Running device whose gateway ARP later failed kept stale normal-font
colors.

### Fix
- `_refresh_device_table_from_database` is now **async**: the per-device
  DB fetch runs in a background QThread (`_DeviceStatusFetchWorker`,
  pinned via the global keepalive), and all widget updates happen on the
  main thread in the new `_apply_device_status_row` slot. No UI blocking.
- Re-enabled `status_timer.start(30000)`. The crashes that justified
  disabling it were the QThread-destruction race fixed in v0.2.24/25
  (global keepalive), and the poll's HTTP is no longer on the UI thread —
  so it's safe again. `poll_device_status` self-adjusts the cadence
  (30 s active / 60 s idle) and only refreshes Running/Starting rows.

### Result
ARP/gateway cells now refresh on a passive view: a gateway goes orange
when its ARP fails and back to normal when it resolves — without a
manual refresh.

### Notes
- Client-only fix (widgets/devices_tab.py).
- Wheel ships as `ostg_trafficgen-0.2.31-py3-none-any.whl`.

## [0.2.30] - 2026-05-27

Audit + fix to ensure a wheel install/upgrade actually activates
every change shipped this release cycle (v0.2.18–v0.2.29).

### Fixed — installer builds the FRR image with --network=host
`install_ostg_complete.py::setup_docker_frr` ran `docker build` /
`docker buildx build` WITHOUT `--network=host`. On corporate-DNS
hosts (Juniper internal / svl-hp-ai-srv02) the Alpine apk fetch in
the build sandbox can't resolve the CDN even though the host can, so
a FRESH install's FRR image build failed on every mirror (the
mirror-retry loop can't fix a DNS-in-sandbox problem). Added
`--network=host` to both build commands — the same flag proven
necessary for the server-side auto-build. Even if this build still
fails, the v0.2.28 server-startup self-heal
(`maybe_rebuild_frr_image`) rebuilds it correctly on first run.

### Verified — upgrade path activates all changes (no code change)
Confirmed end-to-end that a wheel upgrade carries and activates
everything:
- **Wheel contents**: `ostg_docker/Dockerfile.frr` is the Alpine +
  `dhclient` + `dnsmasq` version; `utils/{frr_docker,dhcp,bgp,
  qthread_keepalive}.py`, `run_tgen_server.py`, `run_tgen_client.py`,
  `ostg_docker/{start-frr.sh,frr.conf.template}` and
  `resources/dpdk/install_dpdk.sh` are all packaged.
- **§9a HTTP upgrade** (`/api/admin/upgrade_wheel`) and **SSH
  fallback** both `pip install --upgrade` then `systemctl restart
  netgen-server`. On restart:
    1. `_ensure_frr_assets_deployed()` copies the new Dockerfile.frr
       + ostg_docker tree from the wheel to /opt/netgen.
    2. `maybe_rebuild_frr_image()` (daemon thread) sees the Dockerfile
       SHA changed and rebuilds netgen-frr:latest with the DHCP
       tooling — non-blocking.
  So server code (BGP-VRF, DHCP, FRR self-heal) + Dockerfile + image
  rebuild all land from a plain wheel upgrade.
- **Client** (wheel / DMG / AppImage / exe): `run_tgen_client.py`
  installs the QThread keepalive hook at launch; the fix modules are
  packaged.

### Operator steps after upgrade (unchanged, restated)
- Re-add each BGP device once to clear the stale default-VRF
  `router bgp` block written by the pre-v0.2.26 code (a running
  container is reused, so re-apply alone won't drop it).
- Re-apply DHCP devices so their container is recreated on the
  rebuilt image.

### Notes
- Installer fix (install_ostg_complete.py).
- Wheel ships as `ostg_trafficgen-0.2.30-py3-none-any.whl`.

## [0.2.29] - 2026-05-27

Closes the two minor gaps flagged in the v0.2.28 review.

### DHCP container auto-builds the image when missing
`_ensure_dhcp_container` previously errored "Docker image not found.
Please build the image first." if the FRR image was absent. Since the
DHCP container reuses the FRR image (`_resolve_dhcp_image` →
`_resolve_frr_image`), a DHCP-only deployment that never applied a
BGP/OSPF device (the only path that lazily built the image) could
never start DHCP. Now it calls `_build_frr_image_now()` to build the
image on demand (tags both `netgen-frr:latest` and `ostg-frr:latest`),
then retries. An explicit `NETGEN_DHCP_IMAGE` override that can't be
built still fails cleanly (we honour the override).

### Lazy FRRDockerManager (import no longer needs Docker)
`utils/frr_docker.py` ended with `frr_manager = FRRDockerManager()`,
which called `docker.from_env()` at IMPORT time — so merely importing
the module (directly or transitively) required a running Docker daemon
and a ~1 s connect. Replaced with a transparent lazy proxy
(`_LazyFRRManager`) that instantiates the real manager on first
attribute access. Import is now side-effect-free; all existing
`frr_manager.X` call sites are unchanged. Confirmed: `import
utils.frr_docker` and `import utils.dhcp` now succeed with no Docker
daemon present (previously raised `DockerException` at import).

### Notes
- Server-side fixes (utils/dhcp.py + utils/frr_docker.py).
- Wheel ships as `ostg_trafficgen-0.2.29-py3-none-any.whl`.

## [0.2.28] - 2026-05-27

Closes the gap that made v0.2.27's DHCP fix a no-op on wheel-only
(§9a) upgrades: the FRR image is now rebuilt automatically when the
bundled `Dockerfile.frr` changes.

### The gap
The auto-build (`_try_build_frr_image`) only fires when NO FRR image
exists. A host upgrading from an older wheel already has a
`netgen-frr:latest`, so a Dockerfile change (e.g. v0.2.27 adding
`dhclient`/`dnsmasq`) never took effect — DHCP stayed broken until
the operator manually removed/rebuilt the image. Found during a
post-release bug/gap review.

### Fix
- `_build_frr_image_now()` (extracted from `_try_build_frr_image`)
  now stamps the image with a `netgen.dockerfile_sha` LABEL holding
  the SHA-256 of the Dockerfile it built from.
- New `maybe_rebuild_frr_image()` compares the wheel's current
  Dockerfile SHA against that label and rebuilds when they differ.
- `run_tgen_server.py` `main()` spawns it in a **daemon thread** at
  startup (right after the FRR asset self-heal). Non-blocking — the
  2–3 min build runs in the background while Flask binds its port
  immediately (the v0.2.18 startup-hang lesson, respected). New
  FRR/DHCP containers created after the rebuild completes pick up the
  change; already-running containers keep going until recreated.
- Behaviour matrix: image missing → left to the lazy first-apply
  build (no 2-3 min penalty at every startup on FRR-less hosts);
  image present + SHA matches → no-op; image present + SHA differs →
  background rebuild.

### Verification
On svl-hp-ai-srv02: the running image had no label → stale-check
detected the mismatch → rebuilt with the SHA label → a second check
reported "current" (no redundant rebuild). DHCP tooling
(`dhclient`/`dnsmasq`) confirmed present in the rebuilt image. The
server's image is now labelled, so it won't rebuild again until the
Dockerfile actually changes.

### Review notes (no code change)
- BGP VRF fix (v0.2.26) confirmed complete for the live path — the
  other `router bgp` emitters (`start_bgp`, `build_bgp_cmd`,
  `cleanup_device_routes`, `remove_bgp_config`, …) all early-return
  in docker-FRR mode, so none write default-VRF config.
- DHCP server (dnsmasq) is VRF-compatible: it binds via
  `interface=<dev>` + `bind-interfaces` (SO_BINDTODEVICE), and the
  DHCP container is `network_mode=host`, so it sees the VLAN
  subinterface regardless of VRF membership.
- Minor known gap (not fixed, documented): a DHCP-only deployment
  that never built an FRR image will see `_ensure_dhcp_container`
  error "build the image first" rather than auto-building. Normal
  installs and the startup rebuild cover the common cases.

### Notes
- Server-side fix (utils/frr_docker.py + run_tgen_server.py).
- Wheel ships as `ostg_trafficgen-0.2.28-py3-none-any.whl`.

## [0.2.27] - 2026-05-27

Restore DHCP. v0.2.19's Alpine Dockerfile.frr rewrite silently
dropped the DHCP tooling the old Debian image shipped, breaking
both DHCP client and server modes on every host built since.

### Symptom
Device DHCP (client or server mode) failed. `utils/dhcp.py` shells
out to `dhclient` (client) and `dnsmasq` (server) INSIDE the
container — and the DHCP container reuses the FRR image
(`_resolve_dhcp_image()` → `_resolve_frr_image()`). The Alpine
image only had busybox `udhcpc`; `dhclient` and `dnsmasq` were
absent, so the exec'd commands errored with "not found".

### Cause
The original Debian `Dockerfile.frr` installed `isc-dhcp-client`
and `dnsmasq`. The v0.2.19 Alpine rewrite (which fixed the broken
apt-get build) carried over FRR + networking tools but not the DHCP
packages. Nothing failed loudly — the gap only surfaced when a DHCP
device was applied.

### Fix
`Dockerfile.frr` (and the `ostg_docker/` copy) now install:
- `dhclient` (Alpine `dhclient-4.4.3` — ISC DHCP client; the code's
  IPv6 path already falls back to `dhclient -6` when `dhcp6c` is
  absent, so IPv6 DHCP works too)
- `dnsmasq` (Alpine `dnsmasq-2.90` — the DHCP server backend)

A build-time assertion now FAILS the image build if either binary
is missing after install, so this regression can't silently recur.

### Verification
Rebuilt `netgen-frr:latest` on svl-hp-ai-srv02:
`dhclient → /usr/sbin/dhclient`, `dnsmasq → /usr/sbin/dnsmasq`,
`vtysh → /usr/bin/vtysh` (FRR intact). Build rc=0.

### VRF note
DHCP client is already VRF-aware — `utils/dhcp.py::_migrate_dhcp_route_to_vrf`
moves dhclient's learned default route into the device VRF (the same
per-device VRF used by BGP/OSPF/IS-IS). The DHCP exchange itself is
L2 broadcast on the device interface, so dnsmasq server mode works
regardless of VRF placement.

### Operational note
After upgrading, the FRR image must be rebuilt so new DHCP/FRR
containers pick up the tooling. The server self-heal
(`_try_build_frr_image`) rebuilds it automatically on the next
BGP/OSPF apply when no image is present; to force it, remove the
`netgen-frr:latest` image (or run the §9-documented
`docker build -t netgen-frr:latest -f /opt/netgen/Dockerfile.frr
/opt/netgen`). Already rebuilt on svl-hp-ai-srv02.

### Notes
- Image/server-side fix — client dialogs unchanged.
- Wheel ships as `ostg_trafficgen-0.2.27-py3-none-any.whl`.

## [0.2.26] - 2026-05-27

Fix BGP being configured in the WRONG VRF, which left every BGP
session stuck and never establishing. Found live on svl-hp-ai-srv02
with a VLAN-100 device (vlan100@ens6f1np1 in vrf-3e811e65c12).

### Symptom
`show ip bgp summary` showed the neighbor stuck in `Active`/`Connect`,
`Up/Down: never`. The running-config had TWO half-built BGP
instances for the same ASN:

    router bgp 65000                      ← default VRF: full config
      neighbor 10.x.x.1 ...                 (networks, next-hop-self)
      neighbor 10.x.x.1 update-source 10.x.x.39
      address-family ipv4 unicast
        network 10.x.x.0/24
    router bgp 65000 vrf vrf-3e811e65c12  ← device VRF: stub only
      neighbor 10.x.x.1 ...                 (no address-family, no nets)

The device's `vlan100` interface (and its IP `10.x.x.39`) lives in
`vrf-3e811e65c12`, so the default-VRF instance can't bind its
`update-source` → never connects. The device-VRF instance can reach
the peer but has no address-family activated → never advertises.

### Cause
`utils/bgp.py::configure_bgp_for_device` — the function that emits
the *full* BGP setup (router-id, neighbors, networks, address
families) — hard-coded `router bgp {asn}` with no VRF clause, so it
all landed in the default VRF. A separate neighbor-tweak path
(`_bgp_neighbor_context`) WAS VRF-aware, which is how the stub ended
up in the device VRF — hence the split.

OSPF and IS-IS were already correct (every `router ospf` /
`router isis` block goes through `_ospf_vrf_suffix` /
`_isis_vrf_suffix`). BGP was the lone configurator missing it.

### Fix
`configure_bgp_for_device` now builds the same VRF-aware
`router bgp {asn} vrf {vrf_name}` clause (when the device's VRF link
exists) and emits the entire config under it — matching OSPF/IS-IS
and `_bgp_neighbor_context`. All router-id / network / neighbor /
address-family commands inherit the correct VRF.

### Operational note
After upgrading, **remove and re-add** the affected device (or
restart its FRR container) so the stale default-VRF `router bgp`
block from the old code is cleared — a fresh container starts with
clean config and the new code writes only the device-VRF instance.
Also verify the BGP peer IP is a real, configured neighbor: a
session to a management gateway that isn't running BGP will still
stay down regardless of VRF placement.

### Notes
- Server-side fix (utils/bgp.py) — client dialogs unchanged.
- Wheel ships as `ostg_trafficgen-0.2.26-py3-none-any.whl`.

## [0.2.25] - 2026-05-27

Follow-up to v0.2.24: the keepalive registry's *trim* introduced a
new crash on device-apply. The startup SIGABRT stayed fixed (client
ran for minutes), but clicking Apply aborted with:

    RuntimeError: wrapped C/C++ object of type ArpOperationWorker
    has been deleted
    (in apply_selected_device_with_arp_chain, on .isRunning())

### Cause
v0.2.24's `_trim()` called `deleteLater()` on workers finished
>30 s ago. But a finished worker is often still referenced by a
tab attribute (e.g. `self.arp_operation_worker`). Force-deleting
the C++ object left that Python attribute pointing at a dead
wrapper; the next `.isRunning()` on it raised RuntimeError, which —
unhandled inside a Qt slot — aborts the process.

### Fix
1. `utils/qthread_keepalive.py::_trim()` no longer calls
   `deleteLater()`. It only releases the registry's OWN strong
   reference once a worker has been finished >30 s. Ordinary Python
   refcounting / Qt parent-child cleanup then deletes the C++ object
   when the LAST owner releases it — by which point the thread is
   long done and no wrapper is left dangling. (The registry's only
   real job was to bridge the post-run() teardown race window; after
   that it just gets out of the way.)
2. `widgets/devices_tab.py::apply_selected_device_with_arp_chain`
   now tolerates a stale wrapper defensively: the busy-check is
   wrapped in try/except RuntimeError and treats a deleted worker as
   "free to proceed". It also no longer `deleteLater()`s the previous
   worker itself (same teardown-race risk) — it just clears the
   attribute and lets the registry own teardown.

### Verification
- Unit test: with `_TRIM_AGE_S=0` and the trim forced to run, an
  externally-held worker stays a valid C++ object afterwards (would
  have been deleted under v0.2.24).
- Headless launch with aggressive trim (`_TRIM_AGE_S=3`,
  threshold=5) ran 16 s of startup + poll cycles → CLEAN EXIT rc=0.

### Notes
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.25-py3-none-any.whl`.

## [0.2.24] - 2026-05-27

DEFINITIVE fix for the client startup SIGABRT (v0.2.20–v0.2.23 all
chased the wrong worker). This time the crash was **reproduced
locally headless** (`QT_QPA_PLATFORM=offscreen` against
svl-hp-ai-srv02) so the fix is verified, not guessed.

### Root cause (finally)
The fatal call wasn't Python GC of a worker — it was an **explicit
`worker.deleteLater()` in a `finished` slot**. A Qt message handler
showed the abort firing from inside the event loop (not Python
code), i.e. a queued `deleteLater` being processed on a QThread
whose `run()` had returned but whose internal QThreadPrivate
teardown was still settling → `isRunning()` still true → Qt aborts.

`widgets/l2_emulation_tab.py::_on_worker_finished` did exactly
this, and the L2 sessions refresh timer fires it during startup.
The `[AUTO-START]` / `Fetched 8 interfaces` log lines in every
crash report were red herrings — they just bracket the L2 timer
tick.

Crucially, **no Python-side ref-keeping can prevent this** —
`deleteLater()` destroys the C++ object regardless of how many
Python references exist. v0.2.21–v0.2.23's setParent / keep-ref
approaches couldn't have worked against an explicit deleteLater.

### Fix, in two parts
1. **`utils/qthread_keepalive.py`** (new) — a process-global
   registry. `install()` monkeypatches `QThread.start` so EVERY
   worker app-wide auto-pins a strong ref on start (covers the
   GC-race class of bug for workers we don't individually touch).
   Trims workers >30 s after they finish, when deletion is provably
   safe. Installed once at client launch in `run_tgen_client.py`,
   before the main window (and its startup workers) are built.
2. **Removed every premature `deleteLater` on a QThread** —
   `finished.connect(worker.deleteLater)` connections and
   `worker.deleteLater()` calls inside `finished` slots, across:
     * `widgets/l2_emulation_tab.py` (the startup culprit)
     * `widgets/topology_tab.py` (history fetch, device fetch, SSE)
     * `widgets/devices_tab.py` (SSE worker)
     * `utils/devices_tab_ospf.py` (OSPF apply worker)
     * `utils/devices_tab_bgp.py` (BGP apply worker)
   Lifetime is now owned by the keepalive registry, which only
   deletes a worker once it's been finished long enough that the
   teardown race window has closed.

The known-good sync-wait sites (`stream_logic.py`,
`statistics_section.py`) were also routed through the keepalive
registry instead of immediate deleteLater.

### Verification
Headless launch against srv02 ran 18 s through interface fetch,
stream auto-start, and ~9 stats-poll cycles with devices/topology/L2
tabs all live: `CLEAN EXIT rc=0`, no abort. The same scenario
SIGABRT'd reliably before the fix.

### Notes
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.24-py3-none-any.whl`.

## [0.2.23] - 2026-05-27

Fix yet another QThread SIGABRT site — the stats polling
workers in `statistics_section.py`. Same Python-GC race as
v0.2.21/v0.2.22, different allocation pattern.

The user's log after v0.2.22 still showed:

    Fetched 8 interfaces from http://svl-hp-ai-srv02:5050 (async)
    [AUTO-START] Found 1 enabled stream(s) to auto-start
    QThread: Destroyed while thread is still running

The `[AUTO-START]` log fires inside `_auto_start_streams_from_session`
which then schedules the actual auto-start via
`QTimer.singleShot(100, ...)` — so `_post_traffic_async` (v0.2.22's
target) couldn't be the crash. Process of elimination led to the
stats polling timer (2 sec interval), which fires near-continuously
during startup as the UI initializes.

### Real culprit
`fetch_and_update_statistics()` and `poll_stream_stats()` in
`traffic_client/statistics_section.py` both do:

    self._stats_worker = StatisticsFetchWorker(...)
    self._stats_worker.finished.connect(self._on_stats_fetch_finished)
    self._stats_worker.start()

Every 2 sec the timer fires the next cycle. `self._stats_worker =
StatisticsFetchWorker(...)` drops the Python ref to the PREVIOUS
worker. There's an `isRunning()` guard above the assignment that
returns early if the previous worker is still running — but that
races: `isRunning()` returns false the moment `run()` exits, while
Qt's internal QThreadPrivate cleanup is still in flight. In that
window, Python GC of the wrapper destroys the C++ object → SIGABRT.

### Fix
`self._stats_worker.setParent(self)` immediately after construction
(both sites). PyQt5 checks for a Qt parent on wrapper destruction
and skips the delete if one exists. Also added explicit
`finished.connect(self._stats_worker.deleteLater)` so Qt cleans
up the C++ side on the event loop after the thread fully exits.

Same fix as v0.2.21/v0.2.22 just on a different allocation
shape (assigned to `self.X`, replaced on each timer cycle, vs
local variable that goes out of scope on function return).

### Notes
- The `[AUTO-START]` log is a red herring — it just happens to fall
  between two stats-poll cycles. The actual crash is the OLD
  stats worker getting GC'd when a new one is assigned.
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.23-py3-none-any.whl`.

## [0.2.22] - 2026-05-27

Extends v0.2.21's Qt-parent fix to the three SYNC-wait QThread
sites in `stream_logic.py`. Same SIGABRT, different code path:

    Fetched 8 interfaces from http://svl-hp-ai-srv02:5050 (async)
    [AUTO-START] Found 1 enabled stream(s) to auto-start
    QThread: Destroyed while thread is still running
    zsh: abort      python3 run_tgen_client.py

v0.2.21 fixed it for the async-fetch path (interface-fetch worker
now exits cleanly — note "Fetched 8 interfaces" now prints
BEFORE "[AUTO-START]"). The crash just moved to the stream
auto-start path that runs ~100ms later via
`_do_auto_start_streams` → `start_all_streams` →
`_post_traffic_async`.

### Why these slipped through v0.2.21
v0.2.21 only audited the four async-fetch sites that used the
`finished.connect(cleanup)` pattern. The sync-wait sites in
`stream_logic.py` look superficially safer because they call
`worker.wait()` (which IS instant after `run()` returns) and
THEN `worker.deleteLater()` — but `deleteLater()` only schedules
destruction on the event loop. The function returns before the
event loop processes that delete, the local `worker` falls out
of scope, Python destroys the C++ object, QThread destructor
sees isRunning() still true (Qt internal cleanup mid-flight),
SIGABRT. Exact same race as v0.2.21, just on a different
allocation pattern.

### Fix
`worker.setParent(self)` added before `worker.start()` in all
three sites:
  - `_post_traffic_async` (line ~166) — used by Start/Stop streams
    AND by stream auto-start on session load
  - `_get_async` (line ~194) — used by Edit Stream / Add Stream
    dialogs to fetch RX ports
  - `_pcap_upload_async` (line ~1837) — used by PCAP-mode streams
    to upload .pcap to the server before starting

With a Qt parent, the Python wrapper's GC is a no-op for the
C++ object; `deleteLater()` handles destruction cleanly on the
event loop after `wait()` returns.

### Notes
- Comment at line 170 in v0.2.21 explicitly claimed the
  wait()+deleteLater pattern was safe — "After wait() returns
  the thread is dead, so deleteLater is safe and lets Qt's
  parent/child cleanup release the OS thread + socket handles
  immediately rather than waiting for Python GC to collect the
  local reference." That assumption holds on PyQt5 + Python
  ≤3.13; Python 3.14's GC behavior is stricter and exposed the
  race. Comment now corrected.
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.22-py3-none-any.whl`.

## [0.2.21] - 2026-05-27

Real fix for the QThread SIGABRT on client startup — v0.2.20's
wait()+deleteLater() cleanup didn't actually fix it. Reproduced
again on macOS post-upgrade:

    Fetched 8 interfaces from http://svl-hp-ai-srv02:5050 (async)
    QThread: Destroyed while thread is still running
    zsh: abort      python3 run_tgen_client.py

### Why v0.2.20 failed
The cleanup closure ran `wait() → list.remove(w) → deleteLater()`.
But `deleteLater()` only *schedules* destruction on the event
loop — the actual delete happens on a later loop iteration. The
local `w` variable in the closure went out of scope when the
closure returned (BEFORE the event loop got to the deleteLater),
which dropped the last Python ref. PyQt5's wrapper then noticed
no C++ parent → assumed Python ownership → called the C++
destructor immediately. The QThread destructor saw isRunning()
still true (Qt's internal post-`run()` cleanup hadn't completed)
→ SIGABRT.

### Real fix — Qt-parent ownership
`worker.setParent(self)` transfers C++ ownership to Qt. PyQt5
checks for a Qt parent on wrapper destruction and skips the
delete if one exists. So Python GC of the wrapper becomes a
no-op for the C++ object — Qt owns it. Then
`finished.connect(worker.deleteLater)` lets Qt schedule clean
destruction on the event loop after the thread has fully exited.

Applied to all 4 async-fetch sites that previously had the
half-fix:
  * `menu_actions._fetch_interfaces_async`
  * `menu_actions._RetryAllWorker` loop
  * `server_section` async-fetch (line ~1196)
  * `server_section` server-probe (line ~1439)

The list-tracking (`_menu_iface_workers`, `_server_probe_workers`)
is kept for in-flight bookkeeping but is no longer load-bearing
for lifetime — Qt's parent ownership is. Workers self-remove
from the list in a `finished` slot that does only the list
mutation; deleteLater is a separate connection.

### Notes
- Same idiom already in use at `menu_actions.SaveSessionWorker`
  (line 792) — those four sites just predated the convention.
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.21-py3-none-any.whl`.

## [0.2.20] - 2026-05-27

Hotfix for a startup-time client crash: `python3 run_tgen_client.py`
SIGABRT'd shortly after the first `/api/interfaces` fetch with

    Fetched 8 interfaces from http://svl-hp-ai-srv02:5050 (async)
    QThread: Destroyed while thread is still running
    zsh: abort      python3 run_tgen_client.py

Reproduced live on macOS connecting to svl-hp-ai-srv02:5050.

### Fixed — QThread cleanup in four async-fetch sites
- Root cause: the `finished.connect(lambda: list.remove(worker))`
  pattern dropped the only Python strong ref to the QThread *while*
  Qt's internal bookkeeping (joining the OS thread, releasing
  QThreadPrivate state) was still in flight. Python's GC then deleted
  the C++ QThread object out from under Qt, and Qt's destructor
  saw the thread still marked running → SIGABRT.
- Fix: replace the bare remove-from-list lambda with a cleanup
  closure that does `wait() → list.remove(w) → deleteLater()`. The
  `wait()` is essentially instant since `finished` only fires after
  `run()` returns; it just blocks for Qt's internal join.
  `deleteLater()` then hands ownership of the C++ deletion back
  to Qt's event loop where it belongs.
- Same idiom already in use elsewhere in the codebase
  (`stream_logic.py`, `main.py`); these four sites just predated
  that convention.
- Applied to:
    * `traffic_client/menu_actions.py::_fetch_interfaces_async`
    * `traffic_client/menu_actions.py::_RetryAllWorker` loop
    * `traffic_client/server_section.py` async-fetch (line ~1219)
    * `traffic_client/server_section.py` server-probe (line ~1476)
- Symptom only surfaces when `/api/interfaces` returns FAST enough
  that `finished` fires while the UI thread is still mid-startup,
  which is why this didn't show up in dev until svl-hp-ai-srv02
  came back online with a warm connection pool.

### Notes
- Client-only fix — wheel server code unchanged from v0.2.19.
- Wheel ships as `ostg_trafficgen-0.2.20-py3-none-any.whl`.

## [0.2.19] - 2026-05-27

Hotfix for v0.2.18 — server went offline post-upgrade on
svl-hp-ai-srv02. Three stacked bugs:

1. v0.2.18 called `_try_build_frr_image` from `_resolve_frr_image`,
   which fires at `FRRDockerManager.__init__`. The bgp/ospf/isis
   monitors all instantiate that manager at server startup. So the
   2–3 minute docker build blocked `main()` BEFORE `app.run()` —
   Flask never bound port 5050 and the GUI saw "server offline."
2. The Dockerfile.frr that ships in the wheel (`ostg_docker/`) was
   the OLD Debian/apt-get version that fails because it mixes
   Alpine package names ("nano has no installation candidate") into
   a Debian RUN. The working Alpine Dockerfile.frr lives at the
   repo root and was never in the wheel.
3. The default `docker build` inside the daemon's build sandbox
   couldn't resolve Alpine CDN DNS on hosts behind corporate DNS
   (Juniper internal). Build logs: "WARNING: updating and opening
   https://dl-cdn.alpinelinux.org/alpine/v3.18/main: temporary
   error (try again later)" — 5 retries, then "FRR install failed
   after retries." Host `curl` worked fine, just the sandbox didn't.

### Fixed — auto-build no longer blocks server startup
- `_resolve_frr_image` reverts to pure-lookup. v0.2.18's auto-build
  call removed from here (and the docstring now flags this as a
  must-stay-side-effect-free invariant).
- Auto-build moved into `start_frr_container`: right before
  `client.containers.run(self.image_name, ...)`, do
  `self.client.images.get(self.image_name)`; on `ImageNotFound`,
  call `_try_build_frr_image(self.client)` and update
  `self.image_name` to the new tag. Server startup never blocks;
  operators only pay the 2–3 minute wait on the first BGP/OSPF
  Apply click (where blocking is expected — the GUI shows a spinner).

### Fixed — ship the Alpine Dockerfile.frr in the wheel
- Overwrote `ostg_docker/Dockerfile.frr` with the working Alpine
  version (was: 26-line Debian apt-get install of build-essential
  + ~30 packages, ~10 min build, and broken; now: 30-line Alpine
  apk add of frr + iproute2 + iptables + tooling, ~30 sec build).
- Eliminates the dual-Dockerfile state. `install_ostg_complete.py`'s
  "prefer root, fall back to ostg_docker/" guard still works either
  way — both copies are now identical.

### Fixed — `--network=host` for the auto-build
- `_try_build_frr_image` now passes `network_mode="host"` to
  `client.images.build()`. Equivalent to `docker build --network=host`.
- Without this, the build container uses docker's default bridge
  DNS, which on Juniper internal can't reach Alpine CDN even
  though the host can. Reproduced live on srv02:
  - Host: `curl https://dl-cdn.alpinelinux.org/...APKINDEX.tar.gz`
    → HTTP 200 in 0.99s
  - Build sandbox (default network): same URL → "temporary error
    (try again later)" 5 times → build fails
  - Build with `--network=host`: succeeds in ~30 sec

### Notes
- Manual unblock applied to srv02: stopped service, scp'd Alpine
  Dockerfile, `docker build --network=host`, restarted. Image is
  now present locally so the resolver short-circuits and the
  build path isn't exercised on this box until the operator wipes
  the image.
- Wheel ships as `ostg_trafficgen-0.2.19-py3-none-any.whl`.

## [0.2.18] - 2026-05-26

Closes the "wheel-only upgrade leaves FRR broken" gap that 0.2.17
hit live on svl-hp-ai-srv02: after `pip install --upgrade
ostg_trafficgen-0.2.17.whl`, the BGP/FRR device-apply path failed
with the opaque "FRR manager returned None" because (a) no FRR
Docker image existed on the host and (b) /opt/netgen/Dockerfile.frr
wasn't on disk either, so the operator had to SSH in and rebuild
by hand. v0.2.18 makes the server self-sufficient for both halves.

### Fixed — auto-deploy FRR build assets from the wheel at server startup
- New `_ensure_frr_assets_deployed()` in `run_tgen_server.py`,
  fired from `main()` alongside the existing tx_worker orphan sweep.
- Delegates to `utils.frr_docker._deploy_frr_assets_from_wheel`,
  which imports the freshly-installed `ostg_docker` package, finds
  its on-disk site-packages location via `__file__`, and copies:
    * `Dockerfile.frr` → `/opt/netgen/Dockerfile.frr`
    * `start-frr.sh` → `/opt/netgen/start-frr.sh`
    * `frr.conf.template` → `/opt/netgen/frr.conf.template`
    * Full `ostg_docker/` tree → `/opt/netgen/ostg_docker/`
- Same self-heal pattern as `_ensure_dpdk_tree_deployed`: wheel is
  the canonical source, /opt/netgen/ gets overwritten on every
  startup. Operator customisations to those files are lost (matches
  the existing DPDK contract).
- Best-effort: logs a warning and continues if /opt/netgen/ isn't
  writable. Doesn't block server startup.

### Fixed — auto-build netgen-frr:latest when no FRR image exists locally
- New `_try_build_frr_image(client)` in `utils/frr_docker.py`.
- Wired into `_resolve_frr_image` between "scan local images" and
  "fall back to legacy ostg-frr:latest string". If neither
  `netgen-frr:latest` nor `ostg-frr:latest` is present locally:
    1. Ensure `/opt/netgen/Dockerfile.frr` is in place (calls the
       deploy helper above; idempotent with the startup self-heal).
    2. Run `docker build -t netgen-frr:latest -f Dockerfile.frr .`
       from `/opt/netgen` via the Docker Python SDK (no shell-out).
    3. Also tag the result as `ostg-frr:latest` so legacy callers
       still find an image.
    4. Return the tag on success, None on failure.
- Build takes 2–3 minutes on first run (alpine apk install of frr +
  tooling). Guarded by a module-level `_FRR_BUILD_ATTEMPTED` flag so
  the multi-minute build only runs once per server process — if it
  fails the operator sees the original error on the next apply
  rather than another 3-minute hang.
- On `BuildError`, the helper streams the last 20 lines of the
  build log into the server log so the operator can see WHY it
  failed (apk mirror down, network blip, etc.) without having to
  SSH in and re-run by hand.

### Why this matters
- Old §9a wheel-only upgrade: `pip install --upgrade <new.whl> +
  systemctl restart netgen-server` left /opt/netgen/ frozen at
  whatever the previous install_ostg_complete.py run produced.
  FRR Dockerfile + image got stale (or absent) silently.
- New §9a path: the first time a BGP/OSPF device apply triggers
  the FRR start path, the resolver finds no image, the build
  helper deploys the wheel's Dockerfile + builds the image, and
  the apply proceeds. No operator intervention needed.
- Trade-off: 2–3 minute first-apply latency on a fresh install.
  Acceptable — beats the current "fails opaquely until you SSH
  in" UX.

### Notes
- Both fixes live entirely in the wheel — no install_ostg_complete.py
  changes needed. The wheel-only upgrade path now self-heals.
- Wheel ships as `ostg_trafficgen-0.2.18-py3-none-any.whl`.

## [0.2.17] - 2026-05-27

Quick fix for the device-apply failure operators hit when "VXLAN"
ends up in the protocols list without VXLAN-specific config. Plus
a fix for the prune-old-releases CI job that no-op'd on its first
run.

### Fixed — device/apply 400's on empty VXLAN config
- Real trace from svl-hp-ai-srv02:
  ```
  POST /api/device/apply  →  device1
    Protocols: ['OSPF', 'IS-IS', 'BGP', 'VXLAN']
    VXLAN Config (raw): {}
  VXLAN enabled: config_has_content=False, in_protocols=True, vxlan_config={}
  ERROR: Invalid VXLAN configuration: VXLAN VNI is required
  RESPONSE 400 for POST /api/device/apply
  ```
- Old logic flipped VXLAN enabled=True purely because "VXLAN" was in
  the protocols list. Validation then found no VNI and 400'd the
  WHOLE device apply — operator lost the OSPF/IS-IS/BGP config they
  actually wanted because of one orphaned checkbox.
- New behavior: VXLAN is only "enabled" when the config dict has
  actual content (VNI / remote_peers / explicit enabled=True). If
  "VXLAN" is in protocols but config is empty, log a clear warning,
  drop VXLAN from the active protocols list, and proceed with the
  rest of the device config. Operator sees in the server log:

      WARNING: 'VXLAN' is in protocols list but vxlan_config has no
      VNI / remote peers / enabled=True flag — skipping VXLAN
      configuration. Fix: either remove VXLAN from the device's
      protocols, OR fill in VXLAN VNI + at least one remote peer in
      the device's VXLAN section. The rest of the device config
      (OSPF/IS-IS/BGP/IP) will still apply.

- To force the old strict behavior (intentionally require VXLAN
  config when VXLAN is in protocols), set
  `vxlan_config={"enabled": True}` explicitly.

### Fixed — release.yml prune-old-releases no-op'd on first run
- v0.2.16's prune-old-releases job (introduced in v0.2.15) ran but
  reported "Found 0 releases. Keeping latest 4. Nothing to prune."
- Cause: no `actions/checkout` step in the job and no `--repo` flag
  on the `gh release list` call. Without a checked-out repo, gh has
  no way to know which repo to query.
- Fix: pass `--repo ${{ github.repository }}` explicitly to all three
  `gh` invocations (list, delete, final list). Cheaper than adding
  `actions/checkout` — the job doesn't read any repo files, just
  talks to the GitHub API.
- Confirmed by manually running the equivalent from local: v0.2.5
  release entry deleted (216 MB freed), v0.2.5 git tag preserved.
  After this commit, the next tag push will auto-prune everything
  past KEEP=4 cleanly.

### Notes
- Server-only fix for VXLAN behavior — client-side dialogs unchanged.
- Wheel ships as `ostg_trafficgen-0.2.17-py3-none-any.whl`.
- Operators with broken `device1`-style applies on 0.2.5-0.2.16:
  upgrade to 0.2.17 and re-Apply, OR (as a one-off workaround on the
  current server) untick the "VXLAN" protocol checkbox in the Add
  Device dialog before clicking Apply.

## [0.2.16] - 2026-05-27

Bug fix for the "DPDK stream starts and stops in 1 second" failure
caught live on svl-hp-ai-srv02.

### Fixed — `install_dpdk.sh` symlink-to-self aborts before hugepages
Root cause traced through the full install chain:

  1. Operator clicks `/admin Install DPDK` → script invoked from
     `/opt/netgen/resources/dpdk/install_dpdk.sh`, so SCRIPT_DIR =
     `/opt/netgen/resources/dpdk`.
  2. Steps 1-6 complete successfully — tx_worker builds, links
     against libdpdk 25.11 cleanly.
  3. Step 6's post-build symlink block (added in v0.2.5's b24ec9a)
     loops over canonical paths and does
     `ln -sfn "$_txw" "$canon/tx_worker"`. First iteration:
     `_txw = $canon/tx_worker` = same physical file.
  4. `ln` errors with `'X' and 'X' are the same file`.
  5. `set -euo pipefail` propagates the error.
  6. Script exits BEFORE Step 7 (Configure Hugepages) runs.
  7. `HugePages_Total` stays at 0.
  8. On next DPDK stream start, tx_worker spawns, EAL tries to
     reserve mbufs from hugetlbfs, finds 0 hugepages, exits in
     ~1 s. Stream "starts and stops".

Fix: skip the symlink when source and target are the same file
(detected via bash's `-ef` inode+device test, which also handles
the "symlink already points here" case). Also wraps the `ln`
itself in a `log_warning` fallback so OTHER ln failures
(permission denied on read-only mounts, etc.) don't abort the
install before the critical Step 7.

Operators with broken installs from 0.2.5-0.2.15 can either:

  - Re-run /admin Install DPDK on a v0.2.16 server (the always-sync
    from v0.2.14 will deploy the fixed install_dpdk.sh, then re-run
    the install — this time it gets past Step 6 and reaches Step 7).

  - Manually allocate hugepages once: `sudo sysctl -w vm.nr_hugepages=1024`
    plus `echo "vm.nr_hugepages = 1024" > /etc/sysctl.d/99-netgen-hugepages.conf`.
    (Install Guide section 9b style.)

### Notes
- Server-only fix; no client-side changes.
- Wheel ships as `ostg_trafficgen-0.2.16-py3-none-any.whl`.
- This release will be the first to use the `prune-old-releases`
  job from v0.2.15 — v0.2.5 (oldest milestone) will fall off the
  Releases page automatically.

## [0.2.15] - 2026-05-27

Phase-aware progress UI for `/admin Install DPDK`. Operator clicking
Install DPDK now sees an actual progress bar, current step, elapsed
time, and ETA — instead of staring at a static "log running…"
message that gave no signal until completion 10-20 min later.

### Added — `/admin Install DPDK` progress UI
- New phase indicator above the log pane:
  ```
  Step 5 of 10: Building DPDK              8m 12s elapsed  ~3m remaining
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░  87%
  Compiling DPDK: 2863 / 2954 units (97%)
  ```
- Server-side phase parser scans the install log for the
  `Step N: TITLE` markers `install_dpdk.sh` emits and computes
  overall progress weighted by per-step typical durations
  (Step 5 ninja build dominates at 900s of the 1064s total).
  Sub-step interpolation via the `[X/Y] Compiling` ninja lines so
  the bar fills smoothly during the long step rather than jumping
  in 10% chunks.
- Auto-scroll toggle on the log pane (default on). Respects operator
  scroll position — scrolling up to read an earlier error freezes
  the view; scrolling back to the bottom resumes following the tail.
- Completion state: green 100% bar on `rc=0`, red bar with
  `Failed (rc=N)` label on non-zero exit.
- Page-load auto-resume: refresh the `/admin` page mid-install and
  the progress UI picks up where it left off.

### Performance
- Incremental log fetch via `?offset=N` query param on
  `/api/admin/install_dpdk/log` — server returns only bytes appended
  since the client's last offset (capped at 1 MiB per response).
  Client appends rather than replaces, no more DOM thrash on multi-MB
  logs. Reduces network transfer per poll from ~300 KB to a few KB
  during steady-state ninja compilation.
- DOM cap: client trims log to last 1 MiB once it exceeds 2 MiB,
  with a header line noting how many KB were trimmed and pointing
  at the full log file on the host. Browser stays responsive even
  after multi-hour pathological builds.

### Fixed — 8 bugs caught in pre-tag code review
- **#1** Phase parser was fed only the 64 KiB back-compat tail; on
  long ninja builds the `Step 5` marker scrolled out → bar
  disappeared mid-install. Now reads the full log file (capped
  10 MiB) for phase extraction.
- **#2** `?offset=0` shipped the entire multi-MB log uncapped. Now
  capped at 1 MiB per response with a skip-ahead marker.
- **#3** `log_size` reported pre-read → client's next offset
  under-shot → duplicate appends in the log pane. Now computed
  as `tail_start + len(actual_bytes_read)`.
- **#4** Stale `phase` from previous install showed "Step 10 / 99%"
  indefinitely. Now `phase=null` when not running; rc tells the
  operator the actual outcome.
- **#5** ETA produced nonsense at early polls and step boundaries
  (e.g. "~3m20s" at elapsed=2s, pct=1%). Now suppressed until
  elapsed≥30s AND overall_pct≥5.
- **#6** Concurrent / out-of-order polls double-appended log
  content. Added `pollInFlight` guard plus a server-side
  advancement check (`d.log_size > logOffset`).
- **#7** `log.textContent` grew unbounded. 2 MiB DOM cap added.
- **#8** Auto-scroll yanked operator back to bottom mid-read.
  `isNearBottom(el)` check — only auto-scrolls if within 20 px.

### Notes
- No client-side install/upgrade dialog changes — this release is
  entirely about the `/admin` web UI in netgen-server.
- Wheel ships as `ostg_trafficgen-0.2.15-py3-none-any.whl`.
- Backward compatible: older clients (or curl scripts) using the
  `/api/admin/install_dpdk/log` endpoint without `?offset=` still
  get the 64 KiB `log` field as before.
- Operators on v0.2.14 servers with in-progress installs: upgrading
  mid-install is safe — the new server can read the existing log file
  on disk and resume phase parsing immediately.

## [0.2.14] - 2026-05-27

Server-side fix for the DPDK install path. v0.2.13 fixed
`install_dpdk.sh` itself, but operators upgrading via the §9a
SSH one-liner ended up with a stale copy at `/opt/netgen/` while
the wheel's copy had the fix — and the `/admin` Install DPDK
endpoint launched the stale one. Closes that loop.

### Fixed — `/admin Install DPDK` always syncs from wheel
- `_ensure_dpdk_tree_deployed()` is now called unconditionally
  before the script-resolution step in `api_admin_install_dpdk`,
  not just when the script is missing.
- Why the change: `pip install --upgrade <new.whl> + systemctl
  restart` (Install Guide §9a one-liner) updates the wheel's
  `/usr/local/lib/python3.10/dist-packages/resources/dpdk/`
  copy but doesn't touch `/opt/netgen/resources/dpdk/`. The
  /admin Install DPDK endpoint runs the `/opt/netgen/` copy.
  Earlier self-heal only triggered on MISSING — present-but-stale
  was a no-op.
- Net effect: clicking Install DPDK now resyncs the resources
  tree from the freshly-installed wheel, so the latest
  `install_dpdk.sh` (with any bug fixes) runs every time.
- Trade-off: operator file edits under `/opt/netgen/resources/dpdk/`
  are lost on every Install DPDK click. The design contract is
  "wheel is canonical" — customizations belong in the wheel or
  via env-var overrides, not local file mods.

### Validated against the real failure
- svl-hp-ai-srv02 had 0.2.13 wheel installed (with 8c87de6's
  parent-dir mkdir fix), but `/opt/netgen/resources/dpdk/install_dpdk.sh`
  was the pre-fix version — grep for "mkdir -p" returned only
  the unrelated tx_worker line at 641, not the new line ~250
  block. /admin Install DPDK ran the stale script → identical
  Step 3 "cd: /root/SURAJ: No such file or directory" failure
  that 8c87de6 had explicitly fixed in the wheel.
- After 0.2.14: Install DPDK click → resync from wheel runs
  first → fresh script with the fix → mkdir -p creates the
  parent → git clone proceeds.

### Notes
- No client-side changes — server-only fix.
- Wheel ships as `ostg_trafficgen-0.2.14-py3-none-any.whl`.
- After upgrading to 0.2.14, every Install DPDK click is a
  cheap (~100 KB) idempotent sync. Performance impact: a few ms.

## [0.2.13] - 2026-05-27

Dialog layout cleanup + fix for `/admin Install DPDK` on brand-new
boxes. All five commits target real operator pain points hit live
during the v0.2.12 lab rollout.

### Fixed — `install_dpdk.sh` parent-dir clone failure
- Real failure on svl-hp-ai-srv02 from the /admin Install DPDK
  button on a fresh box (no `$HOME/SURAJ/` pre-staged):

      Step 3: Cloning DPDK
      [INFO] Cloning DPDK to: /root/SURAJ/dpdk
      install_dpdk.sh: line 250: cd: /root/SURAJ: No such file or directory
      [install died here — git clone never ran]

- `step_clone_dpdk` did `cd $(dirname $DPDK_DIR)` straight off, with
  no `mkdir -p`. Worked fine on dev boxes where SURAJ/ already
  existed from earlier manual setup; failed silently on every new
  box.
- Fix: `mkdir -p` the parent before cd, with a clear
  permission-denied path. Also handles the existing-dir case:
  reuse the clone if it's already a git repo, rm-rf and re-clone
  if it's a half-finished previous attempt. Better error message
  on git clone failure (mentions the pre-stage / DPDK_DIR
  workarounds for firewalled labs).

### Added — Install/Upgrade dialog: chassis history dropdowns
- Server URL (Upgrade tab) and Host (Fresh Install tab) fields
  converted from `QLineEdit` to editable `QComboBox` widgets seeded
  from `~/.netgen/chassis_history.json` (the same store Add TGEN
  Chassis uses). Operators who've already added their lab boxes
  there can pick from the dropdown instead of retyping; typing a
  new address still works.
- Upgrade tab items show `http://host:port — label`; Fresh Install
  tab items show `host — label` and picking one also bumps the
  port spinbox to whatever was stored.
- `InsertPolicy.NoInsert` — typed text doesn't auto-add to the
  dropdown, so operator typing doesn't pollute the history list.

### Fixed — Install/Upgrade dialog: overlapping fields
- Three rounds of layout cleanup after operator screenshots
  showed fields rendering on top of each other:
  - **Upgrade tab SSH fallback**: replaced checkable QGroupBox
    (only `setEnabled` children when off) with a plain header
    checkbox controlling a `QWidget` wrapper's visibility. Off
    state collapses to a single 1-line header instead of the full
    4-row form. Saves ~140 px vertical when not in use.
  - **Fresh Install tab auth rows**: Password and Key file rows
    wrapped in container widgets; `_update_auth_visibility` now
    hides the inactive row instead of just disabling it. Saves
    ~30 px.
  - **Fresh Install tab flags group**: tightened margins (8,4,8,4)
    and spacing (2); shortened the 3-sentence footer paragraph to
    1 line. Saves ~70 px total.
  - **Upgrade tab SSH fallback auth rows**: same hide-don't-disable
    fix as Fresh Install. Saves ~30 px.

### Notes
- No server-side schema changes; chassis history reads work
  against any /api/health endpoint that returns netgen_version
  (0.2.12+). Older servers still display amber `?` in the Version
  column.
- Wheel ships as `ostg_trafficgen-0.2.13-py3-none-any.whl`.
- Validated `install_dpdk.sh` diagnosis against svl-hp-ai-srv02's
  `/tmp/netgen_install_dpdk_*.log` — script truncated at exactly
  the cd line, confirming the failure path before fix.

## [0.2.12] - 2026-05-26

The "no more chicken-and-egg" release. v0.2.11 fixed the broken
upgrade endpoint but left the manual-one-time-upgrade burden on
operators; v0.2.12 closes the loop by making the *client* able to
upgrade a still-broken server, and the *server* able to self-heal
its DPDK install tree from the wheel.

### Added — Upgrade tab now has an SSH fallback
- When POST `/api/admin/upgrade_wheel` returns HTTP 5xx (the latent
  v0.2.6–v0.2.10 NameError, or any future server-side bug) OR the
  HTTP connect itself fails, the dialog can automatically fall
  back to a paramiko-based pip-install + systemctl restart.
- New collapsible "SSH fallback" section in the Upgrade tab —
  default OFF (the first attempt stays pure HTTP, fastest path for
  healthy servers). Operator fills user / port / password OR SSH
  key once and forgets it.
- New `WheelUploadWorker.http_endpoint_broken` signal carries a
  short reason string for the dialog to surface. 4xx still routes
  to the existing failure dialog (4xx = bad input, SSH fallback
  wouldn't help).
- New `SshUpgradeWorker` class: minimal paramiko-based pip + restart.
  ~10s round-trip vs 5-45 min for the Fresh Install tab. Uses a PTY
  (no nohup detach) since the 10-second upgrade doesn't need to
  survive client disconnects.
- Dialog log shows the transition explicitly:
  ```
  [client] HTTP endpoint failed: server returned HTTP 500 ...
  [client] Falling back to SSH-based pip install + restart (Install Guide §9a)...
  [ssh-upgrade] auth: password
  [ssh-upgrade] sftp put .../ostg_trafficgen-0.2.12-py3-none-any.whl
  [ssh-upgrade] exec: pip3 install --upgrade ... && systemctl restart ...
  Successfully installed ostg-trafficgen-0.2.12
  [ssh-upgrade] exit rc=0
  ```

### Added — Server self-heal for `/api/admin/install_dpdk`
- New `_ensure_dpdk_tree_deployed()` helper. When the `/admin`
  Install DPDK button (or any `/api/dpdk/*` endpoint) needs
  `install_dpdk.sh` and can't find it at `/opt/netgen/resources/dpdk/`
  or the legacy `/opt/OSTG/...` path, the server now:
  1. Imports `resources.dpdk` to locate the wheel's pip-install
     directory (handles system / venv / pipx layouts via `__file__`).
  2. `shutil.copytree`'s the tree to `/opt/netgen/resources/dpdk/`.
  3. Preserves the executable bit on .sh scripts.
- Net effect: operators who installed via bare `pip install <wheel>`
  (or the pre-v0.2.12 dialog that didn't sftp the resources tree)
  can now click Install DPDK in `/admin` and the server fixes its
  own state before launching the script. No more "install_dpdk.sh
  not found" 404 with manual `scp resources/dpdk/...` recovery.
- Validated live on svl-hp-ai-srv04: deliberately renamed
  `/opt/netgen/resources/dpdk/` to `.bak`, called the endpoint,
  HTTP 200 returned + tree was recreated from site-packages.

### Added — Installer extracts bundled assets from the wheel
- `install_ostg_complete.py`'s `install_ostg()` step runs a Python
  heredoc on the target after `pip install <wheel>` that imports
  `resources.dpdk` and `ostg_docker` and copies their on-disk
  locations into `/opt/netgen/`. Also publishes `Dockerfile.frr`,
  `start-frr.sh`, `frr.conf.template` at the install root.
- Makes the dialog Fresh Install flow (which sftps only the wheel
  + installer) functionally identical to a full source-tree
  install. The `script_dir`-based copy code below is preserved as
  a fallback for very old install paths but no longer load-bearing.

### Added — Fresh Install dialog sftps support files
- `SshInstallWorker.run()` now also uploads, after the wheel +
  installer:
  - `Dockerfile.frr` (FRR Alpine container recipe)
  - `requirements.txt`
  - `resources/dpdk/` (full recursive tree — DPDK scripts +
    tx_worker source)
  - `ostg_docker/` (full recursive tree — frr.conf.template,
    start-frr.sh, etc.)
- New `_sftp_put_tree` helper: walks a local directory, recreates
  the structure on the target, skips `__pycache__` + dotfiles,
  preserves the executable bit on .sh/.py via sftp.chmod(0o755).
- Missing local files log a `[warn]` and continue — operator may
  not need all features on a given install (e.g. client-only
  setups don't need Dockerfile.frr).

### Added — Server version column in Add TGEN Chassis dialog
- `/api/health` response gains a `netgen_version` field, parsed
  from pip metadata via `importlib.metadata.version("ostg-trafficgen")`.
  Falls back to `"unknown"` if metadata isn't readable.
- History table in Add TGEN Chassis dialog grows a "Version"
  column (col 4). Populated by the existing ReachabilityWorker —
  when the probe returns 200, parse `netgen_version` from the JSON
  and write it into the row.
- Three colour states for at-a-glance scanning:
  - **black** — server reachable and exposes version (0.2.12+)
  - **amber `?`** — server reachable but old (0.2.11 or earlier)
  - **grey `?`** — server unreachable / probe failed / untested
- Tooltip on each cell explains the state. Operators get a visual
  checklist of boxes that still need upgrading.

### Notes
- **No mandatory upgrade for v0.2.6–v0.2.11 servers.** v0.2.11 was
  the mandatory one (fixed the upgrade endpoint NameError); v0.2.12
  is purely additive on the server side. The wheel is backwards-
  compatible.
- **The client's Upgrade tab now works against v0.2.6–v0.2.11
  servers** when SSH fallback is enabled — the broken endpoint
  triggers the auto-fallback to paramiko-based pip install. This
  removes the "must do one manual upgrade per box" friction from
  the v0.2.11 release notes.
- Wheel ships as `ostg_trafficgen-0.2.12-py3-none-any.whl`.

## [0.2.11] - 2026-05-26

**Mandatory upgrade if your server runs any 0.2.6–0.2.10 build.**
The in-GUI Upgrade tab was bricked by a latent NameError on the
server side; this release fixes it. Also bundles all install-dialog
support files so a Fresh Install actually deploys DPDK + FRR.

### Fixed — `/api/admin/upgrade_wheel` NameError
- Server-side handler (shipped in 0.2.6) used `sys.executable` to
  invoke pip with the same Python interpreter the server runs
  under. But `sys` was never imported anywhere in run_tgen_server.py
  (15k+ LOC, no other reference). Every call to the Upgrade tab
  returned a bare HTTP 500 with this stack trace:

      File "run_tgen_server.py", line 14085, in api_admin_upgrade_wheel
          py = sys.executable or "python3"
      NameError: name 'sys' is not defined

- Bug went unnoticed for four releases because the OTHER
  install-dialog bugs (apt-wait infinite loop, Python stdout
  buffering, --wheel path mismatch) blocked operators from
  actually reaching this code path. With 0.2.10's fixes the dialog
  finally got far enough to hit it.
- Fix: `import sys` inline at the call site (commit a882229).
  Chicken-and-egg: operators on 0.2.6–0.2.10 must do this one
  upgrade *manually* via SSH (Install Guide section 9a) before
  the Upgrade tab works. Once on 0.2.11+, subsequent upgrades
  can use Tab 1 cleanly.

### Added — Fresh Install dialog now ships support files
- The dialog used to sftp only the wheel + install_ostg_complete.py.
  The installer then logged warnings + errors trying to read
  sibling files that weren't on the target:
  - `WARNING: resources/dpdk/ not found — DPDK bind/unbind endpoints will return 404`
  - `ERROR: failed to read dockerfile: open Dockerfile.frr: no such file or directory`
- The install completed (script is tolerant) but the target ended
  up without DPDK scripts at /opt/netgen/resources/dpdk/ and without
  a netgen-frr Docker image. Operators had to manually scp the
  missing files or re-run install_ostg_complete.py from a full
  source checkout.
- Fix: SshInstallWorker.run() now also uploads, after the wheel +
  installer:
  - `Dockerfile.frr` (FRR Alpine container recipe)
  - `requirements.txt`
  - `resources/dpdk/` (full recursive tree — DPDK scripts +
    tx_worker source)
  - `ostg_docker/` (full recursive tree — frr.conf.template,
    start-frr.sh, etc.)
- New `_sftp_put_tree` helper walks a local directory, recreates
  the structure on the target, skips __pycache__ + dotfiles,
  preserves the executable bit on .sh/.py files.
- Missing local files log a `[warn]` and continue — operator may
  not need all features on a given install (e.g. client-only
  setups don't need Dockerfile.frr).

### Documentation
- Install Guide section 9 (Reinstall / upgrade) expanded from a
  3-line table into a full walkthrough with five subsections:
  - **9a. Manual SSH one-liner** ★ — `scp + ssh + pip install + restart`,
    each flag explained
  - **9b. Step-by-step on the box** — numbered 1-7 interactive
    walkthrough with shell snippets and the "skip restart →
    server runs old code in memory" footgun
  - **9c. Rollback** — one-liner pattern + "keep last 2-3 wheels"
    tip for bisecting regressions
  - **9d. Full re-provision** — install_ostg_complete.py rerun
  - **9e. When the Upgrade tab is broken** — chicken-and-egg
    recovery; explicitly calls out the 0.2.6–0.2.10 NameError
    bug for operators reading the guide on those versions

### Notes
- No new dependencies on either side.
- Wheel ships as `ostg_trafficgen-0.2.11-py3-none-any.whl`.
- Validated end-to-end on svl-hp-ai-srv04: hot-patched the sys
  import → POST `/api/admin/upgrade_wheel` returned 200 →
  background pip install completed → GET log poll triggered
  systemctl restart → fresh PID, /api/health returned 200.
- svl-hp-ai-srv02 manually upgraded to 0.2.10 via the new
  section 9a one-liner end-to-end, validating the doc against
  a real operator workflow.

## [0.2.10] - 2026-05-26

Install dialog visibility fixes — three bugs that made successful
installs LOOK like failures to the operator. All three reproduced
live on svl-hp-ai-srv04 during a real v0.2.7 → v0.2.9 upgrade
attempt; this release fixes them.

### Added — pop-out log window
- New **"Pop out ↗"** button in the Log group box header of the
  Install / Upgrade Server dialog. Opens a detached 1100×720
  QDialog with a much larger log view — both windows share the
  same QTextDocument via setDocument(), so any appendHtml /
  appendPlainText / clear() on the embedded view shows in both.
  Non-modal: the operator keeps interacting with the main dialog
  (Install / Test Connection / etc.) while watching output stream
  in the popout alongside.
- Dark-mode styling (#0f172a background, #e2e8f0 text, 12pt
  monospace) — easier on the eyes for the 15-min DPDK build than
  the cramped light-mode pane.
- Auto-scroll toggle (default ON) — freezeable so the operator
  can read carefully while the install keeps appending output.
- Toggle button label flips between "Pop out ↗" / "Focus popout ↗"
  based on state. Parent dialog closeEvent tears down the popout
  so no orphan top-level window survives.

### Fixed — infinite apt-wait via self-matching pgrep
- `_wait_for_apt_lock` ran `pgrep -f '(apt|dpkg)'` via
  `subprocess.run(..., shell=True)`, which executes as
  `sh -c "pgrep -f '(apt|dpkg)'"`. The wrapper's own command line
  contains the literal characters "apt" and "dpkg" — so pgrep -f
  (which regex-matches the full cmdline of every process) always
  matched the wrapper itself and returned a hit. The check was
  effectively `while true: log("waiting...")` until the 5-min
  timeout fired and proceeded with a WARNING. Operators gave up
  watching long before that.
- Fix: switch to `fuser /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend
  /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null`.
  fuser only flags a process if the kernel reports it has the lock
  file open via an fd. The fuser binary itself doesn't open the
  lock files, so no self-match. This is what apt itself uses
  internally.
- Bonus diagnostic: when a real holder IS found, the log now
  resolves the pid → cmdline via `ps -o pid,etime,cmd -p <pid>`
  and surfaces "Waiting for apt/dpkg lock to release... (10s) —
  holder: 2291 3-04:11 /usr/share/unattended-upgrades/..." so
  operators see WHO is blocking them.

### Fixed — log stream frozen for entire install duration
- `install_ostg_complete.py` uses `print(...)` for its [INFO] /
  [WARNING] / [ERROR] lines. When stdout is redirected to a file
  (the wrapper does this via `>> /var/log/netgen-install.log
  2>&1`), Python detects "not a tty" and switches print() to
  FULL buffering — lines accumulate in Python's userspace buffer
  and don't reach the file until either the buffer fills (4-8 KB)
  or Python exits.
- Meanwhile subprocess.run() output bypasses Python's stdout
  buffer entirely (inherits the parent's fd directly). So
  systemctl/docker errors appeared instantly, while every [INFO]
  line stayed invisible until the install finally exited.
- The dialog's poll loop showed cleanup output ending at
  "exit status 1" and then APPEARED FROZEN for 5-10 minutes.
  Operators consistently concluded the install had failed and
  killed it.
- Fix: invoke the installer as `python3 -u install_ostg_complete.py`
  in the dialog's spawn wrapper. `-u` forces unbuffered
  stdout/stderr — each print() reaches the file fd immediately,
  the poll loop surfaces it within 1-5 s.

### Notes
- No new dependencies on either side.
- Wheel ships as `ostg_trafficgen-0.2.10-py3-none-any.whl`.
- The detached-install wrapper (`/var/log/netgen-install.log`,
  `/var/run/netgen-install.pid`, `/var/run/netgen-install.exit`)
  introduced in 0.2.8 is unchanged. Only the python3 invocation
  inside it picks up the -u flag.
- Validated end-to-end: v0.2.7 → v0.2.10 upgrade via the dialog
  flow on svl-hp-ai-srv04 now streams log lines live (no more
  "appears stuck" UX) and the apt-wait check passes immediately
  when no real lock is held.

## [0.2.9] - 2026-05-17

Operator-workflow improvements across the install dialog + chassis
manager. No behavioral changes to traffic generation or DPDK —
this release is entirely about the click-paths around them.

### Added — Install / Upgrade Server dialog
- **SSH port field** (defaults to 22) — lab boxes behind jump hosts
  or alternate sshd configs (2222 / 22000 / etc.) were unreachable
  from the dialog before. Threaded through `SshInstallWorker` and
  `_probe_existing_install`.
- **Test Connection button** — runs the SSH connect + four cheap
  pre-flight probes in 2-3 s:
  - Python ≥ 3.9 on the target (`install_python_dependencies`
    needs it)
  - Sudo capability (`sudo -n true`) for non-root users
  - Free disk ≥ 4 GB on /var (DPDK build + FRR Docker + apt cache)
  - Is `netgen-server` already active? If yes, hints to switch to
    the Upgrade tab (30 s round-trip) instead of a 15-min full
    install.
  - Each probe logs ✓/✗ to the log pane; tail QMessageBox shows
    pass/fail summary. Catches the obvious bugs (wrong password,
    wrong port, no sudo, Python 3.8) in 2 s instead of 30 s into
    spawn.

### Added — error surfacing
- **Log pane is now color-coded** as output streams in:
  - red `#dc2626` — `[ERROR]`, `error`, `exception`, `failed`,
    `fatal`, `traceback`, `-E-`, `✗`
  - amber `#d97706` — `[WARNING]`, `warn`, `⚠`
  - green `#15803d` — `✓`, `success`, `OK`
  - neutral — everything else
  - Explicit bracket tags (`[ERROR]`, `[WARN]`) win over the noise
    heuristic, so `[ERROR] Wheel file not found: dist/...` stays
    red even though "not found" matches the noise filter.
  - ANSI escape sequences (`\x1b[...m`) from `install_dpdk.sh`'s
    colored stdout get stripped before we re-color via HTML.
- **Failure dialog now shows the actual error**, not "see log":
  `QMessageBox.critical` bullets the last 6 captured error lines
  so operators no longer have to scroll a 1000-line log to find
  the proximate cause. Used by both upgrade (Tab 1) and SSH
  install (Tab 2) failure paths.
- **Cleanup-style noise filtered** — `Failed to stop ostg-server:
  Unit not loaded`, `No such image: ostg-frr:latest`, and
  similar legitimate "this is fine" cleanup messages don't get
  highlighted as errors. Regex tuned against 13 real log lines
  from operator installs.

### Added — Add TGEN Chassis dialog
- **"Add to TGen List" button** (new, between Add to History and
  Connect & Add) — adds the chassis to the main TGen tree on the
  left without attempting a connection. Marked offline initially;
  the periodic health worker still probes and flips it online
  when `/api/health` responds. Stays open so the operator can
  queue several before clicking Close.
- Multi-select queueing — the chosen-connections list grows
  across Add to TGen List clicks; Close applies them all in one
  `update_server_tree()` redraw.
- `connect_now: bool` field on chosen-connection entries lets the
  dialog distinguish "add and connect" from "add only". Default
  True for backward compat with Connect Selected / Connect & Add.

### Notes
- No new dependencies on either side.
- Wheel ships as `ostg_trafficgen-0.2.9-py3-none-any.whl`.
- Pre-flight probes validated against svl-hp-ai-srv04 — all four
  return expected results (Python 3.10.12, sudo as root, 1777 GB
  free, netgen-server already active → Upgrade-tab hint).

## [0.2.8] - 2026-05-17

Detached install flow + the bug fixes that made it work end-to-end.
0.2.7's Fresh Install dialog worked in theory but had three gaps
(install died on client exit, `--wheel` path was misrouted, upgrade
didn't restart the live server). All three closed; the in-GUI
dialog now does a full v0.2.5 → v0.2.x in-place upgrade and the
operator sees the new behavior immediately.

### Added — detached install survives client exit
- **`SshInstallWorker` rewritten** around `nohup` + log polling
  instead of PTY-streamed `exec_command`:
  - Wrapper script writes pid to `/var/run/netgen-install.pid`,
    runs the installer with output appended to
    `/var/log/netgen-install.log`, captures rc into
    `/var/run/netgen-install.exit`, cleans up the pid file.
  - `nohup sh -c '...' < /dev/null > /dev/null 2>&1 &` — no PTY,
    no SIGHUP on disconnect. Install survives client crash, WiFi
    blip, dialog close.
  - Client polls the log incrementally (`tail -c +<offset+1>`) with
    adaptive backoff (1 s while output flows, 5 s idle).
  - Transient SSH errors during the poll loop logged but don't
    abort (sshd restart during apt installs recovers gracefully).
- **Resume monitoring** — `AddTGenDialog._probe_existing_install()`
  runs a 2-3 s blocking SSH probe at click-time. When a live
  install is found on the target, prompts: *"A previous install
  is still running on host (pid N). Resume monitoring its log?"*
  Worker switches to `resume_mode=True`, skips SFTP + spawn, jumps
  straight to the poll loop.
- **`closeEvent` differentiates worker types**: detached SSH says
  *"closing this dialog will stop monitoring the log, but the
  install itself will continue to completion"*; foreground HTTP
  upgrade keeps the old *"abort and may leave server inconsistent"*
  copy (no way to detach a mid-flight HTTP upload).

### Fixed — `--wheel` early-out also sets `_actual_wheel_file`
- `_build_wheel()`'s early-out for the `--wheel <path>` flag
  set `self._actual_wheel_path` but missed setting
  `self._actual_wheel_file`. `install_ostg()` then read both:
  ```python
  local_wheel_path = getattr(self, "_actual_wheel_path", None)
  wheel_file       = getattr(self, "_actual_wheel_file", None)
  if not local_wheel_path or not wheel_file:
      # broken fallback
  ```
  Missing `wheel_file` triggered the fallback, which computed
  `dist/ostg_trafficgen-{WHEEL_VERSION}-...whl` — and
  `WHEEL_VERSION` on the target is `0.0.0` (the no-pyproject.toml
  parse fallback). Result: misleading
  `"Wheel file not found: dist/ostg_trafficgen-0.0.0-py3-none-any.whl"`
  error and rc=1. One-line fix: also set
  `self._actual_wheel_file = os.path.basename(pw)` in the early-out.

### Fixed — `start_ostg_services` restarts when already active
- Upgrade installs (pip-installing a new wheel onto a server that
  was already running) used to leave the *old* code in memory:
  `systemctl start <unit>` is a no-op on an already-active unit,
  so the running process was never bounced. Operators saw
  `pip3 show ostg-trafficgen` report the new version while the
  live behavior was still the old one.
- `start_ostg_services()` now probes `systemctl is-active` first.
  If the unit is active, switches to `systemctl restart` so the
  process is forcibly bounced with the new code in memory. If
  inactive/failed/missing, does the original cold `start`. Both
  paths log the decision so the operator can see in the install
  transcript whether it was a fresh boot or an upgrade reload.

### Documentation
- `Help → Install Guide` section 1 (In-GUI installer) extended
  with a new "Detached install — what survives client exit"
  subsection: target-state file table (`/var/log/netgen-install.log`,
  `/var/run/netgen-install.pid`, `/var/run/netgen-install.exit`),
  the resume-monitoring flow, and the differentiated `closeEvent`
  copy split per tab. Safety properties rewritten as a bullet
  list separating HTTP-tab and SSH-tab guarantees.

### Notes
- No new dependencies on either side.
- Wheel ships as `ostg_trafficgen-0.2.8-py3-none-any.whl`.
- Lab box `svl-hp-ai-srv04` validated end-to-end: 0.2.5 → 0.2.7
  via dialog → exposed the three bugs above → fixed → 0.2.5 →
  0.2.8 dialog upgrade should now complete cleanly without
  manual intervention.

## [0.2.7] - 2026-05-16

Operator-quality-of-life release. Spirent-style chassis manager,
in-GUI access to the server admin console, real removal cleanup in
the Traffic Stats pane, and a self-healing `tx_worker` install path.

### Added — Spirent-style Add TGEN Chassis dialog
- **File → Add TGEN Chassis (Ctrl+N)** now opens a proper chassis
  manager instead of two bare `QInputDialog.getText` prompts. New
  `widgets/add_tgen_dialog.py` (~620 LOC):
  - **Recent connections** table — address, port, label, last-
    connected timestamp ("3 min ago"), reachability LED (✓/✗/?),
    connect count. Auto-probes `/api/health` on open via a
    background `QThread` so LEDs go live without clicking Test all.
  - **Multi-select connect** — Ctrl/Shift-click rows, "Connect N
    Selected" button label shows the count, single-shot bulk-adds
    every selected chassis with one `update_server_tree()` redraw.
  - **Add to History** — saves the form to
    `~/.netgen/chassis_history.json` without connecting, clears the
    form, stays open so the operator can pre-stage several chassis.
  - **Open Admin Console** — opens `http://<chassis>:<port>/admin`
    in the default browser via `QDesktopServices.openUrl`. Resolves
    the URL from the highlighted row, or the connection form if no
    row is selected. `/admin` is auth-exempt so no token typing
    required.
  - **Test all** re-probes reachability on demand.
  - **Remove from history** (with confirm prompt) drops rows from
    `chassis_history.json` without touching connected servers.
- Persistence at `~/.netgen/chassis_history.json` — capped at 50
  entries, sorted by `last_connected` desc, auth tokens explicitly
  not persisted (session-only, re-entered each session).
- `traffic_client/menu_actions.py` `add_server_interface()` rewritten
  to consume the dialog's `chosen_connections` list — loops adds,
  skips already-connected URLs with a single tail info dialog, fires
  exactly one `update_server_tree()` + `save_server_interfaces()`
  per batch.

### Fixed — Traffic Stats survive a TGen removal
- Removing a TGen chassis from the server tree now scrubs every
  cache and table cell tied to that TG, in lockstep:
  - `_pending_stats_data[server_address]` — current-cycle fetch buf
  - `_pending_stream_stats` / `_pending_poll_stream_stats` — stream
    lists filtered by `_tg_id`
  - `_last_statistics["TG <id> - <iface>"]` — persistent fallback
    cache (the killer — without this, removed-TG rows would re-paint
    on the next "empty fetch" tick and never go away)
  - `_iface_baselines` / `_stream_baselines` — Clear Stats baselines
    (so a re-added chassis with the same TG id doesn't inherit
    stale baselines and report negative deltas)
  - `self.streams` — main Streams configuration table source
  - `ThroughputChart._samples` — Live Chart history (via new
    `remove_iface_by_prefix("TG <id> - ")` method)
- New `StatisticsSection.reset_statistics_table_structure()` does a
  real hard reset (`setColumnCount(0)` + `setHorizontalHeaderLabels([])`
  + `setRowCount(0)` on the stream stats table + chart clear).
  Called when `server_interfaces` becomes empty, distinct from the
  existing `clear_statistics_table()` which is deliberately a soft
  clear (zeros cells, keeps columns) for the Clear Stats button flow.
- Three previously-broken call sites that called the soft clear when
  they should have hard-reset are now wired to `reset_statistics_table_structure`:
  `fetch_and_update_statistics()` no-servers branch,
  `_on_stats_fetch_finished()` empty-stats branch (both inner cases),
  and the new `prune_server_stats()`.

### Fixed — `install_dpdk.sh` tx_worker path mismatch
- When operators run `install_dpdk.sh` from a git checkout
  (`/root/netgen/resources/dpdk/install_dpdk.sh`), `SCRIPT_DIR`
  resolves to that checkout and `meson build` lands at
  `/root/netgen/.../tx_worker/build/tx_worker`. But the server's
  `/api/admin/health` probe only walks `/opt/netgen/`, `/opt/OSTG/`,
  and `/usr/local/bin/tx_worker` — so the admin portal reports
  "tx_worker binary Not built" even though the binary works.
- `step_build_tx_worker` now drops symlinks into both canonical
  install roots (when their parents exist) and installs a copy at
  `/usr/local/bin/tx_worker`. Self-healing across re-runs and across
  the OSTG → netgen rebrand.

### Documentation
- **Help → Install Guide** rewritten:
  - New section "1. In-GUI installer (NEW in 0.2.6) ★ recommended"
    documenting the Help → Install / Upgrade Server dialog from both
    tabs (Upgrade running server / Fresh install via SSH) with
    safety properties.
  - New section "2. Prebuilt release artifacts" with a three-column
    table (File / Contains / Runs on) that explicitly spells out
    the wheel ships BOTH server + client + CLI while the
    .dmg / .exe / .AppImage are client-GUI-only bundles. Includes
    a "Why no Server bundle?" subsection covering Linux-only
    dependencies and a platform-vs-command compatibility table.
  - New section "9. Reinstall / upgrade" rewritten as a method-vs-time
    table covering the in-GUI Tab 1 (30-60 s), manual pip-install
    over SSH (~10 s), and full `install_ostg_complete.py` re-run
    (5-10 min) paths.
  - Existing sections renumbered 3-8.

### Notes
- No new dependencies on either side. Client uses `QDesktopServices`
  + `paramiko` (both already in `requirements.txt`).
- Wheel still ships as `ostg_trafficgen-0.2.7-py3-none-any.whl` for
  pip-install backwards compatibility.

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
