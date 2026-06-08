# Changelog

All notable changes to OSTG / Netgen Traffic Generator will be documented in this file.

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
