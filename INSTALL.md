# Netgen Install Guide

## Quickstart — pick one

### Path A — Install from the client (easiest, no SSH typing)

If you have a Linux box you can SSH into + your operator laptop, the client can drive the whole install for you:

1. **Install the client** on your laptop — download from the latest GH release:
   - macOS — `Netgen-TrafficGenerator-<v>.dmg` (drag to Applications)
   - Windows — `Netgen-Client-<v>-windows.exe` (double-click)
   - Linux — `Netgen-Client-<v>-linux-x86_64.AppImage` (`chmod +x` + run)
2. **Launch the client** and (if prompted) activate the license — see [License activation](#license-activation)
3. Open **Help → Install / Upgrade Server…**
4. Switch to the **Fresh install via SSH** tab
5. Enter the target Linux host + SSH user + key/password
6. Click **Install**. The dialog SFTPs the bundled **wheel** (`ostg_trafficgen-<v>-py3-none-any.whl`) and the bundled `install_ostg_complete.py` to `/tmp/netgen_install/` on the host, runs the installer over SSH, and streams the live log inline
7. When done, **Tools → Add Server** → enter the server URL → green LED = ready

The client bundles the wheel + installer script, so step 1 covers everything — no separate download. **For the bundled-venv tarball flow (recommended for v0.5.x), use Path B or point the "Wheel / tarball" field at a `netgen-server-*.tar.gz` you download separately.**

### Path B — Direct on the Linux server (no laptop needed)

```bash
VER=$(curl -s https://api.github.com/repos/amishagrawal2001-arch/netgen/releases/latest \
       | grep -oP '"tag_name": "v\K[^"]+')
wget https://github.com/amishagrawal2001-arch/netgen/releases/download/v${VER}/netgen-server-${VER}-linux-x86_64.tar.gz
sudo mkdir -p /opt/netgen-server && \
  sudo tar -xzf netgen-server-*-linux-x86_64.tar.gz -C /opt/netgen-server --strip-components=1 && \
  sudo /opt/netgen-server/bin/netgen-install
```

Verify:

```bash
systemctl status netgen-server
curl -s http://localhost:5050/api/admin/health | jq .health   # "healthy"
```

**If `wget` 404s:** the tarball auto-build was disabled between v0.5.22 and v0.5.186; only v0.5.187+ tags produce a tarball on the release page automatically. For older tags, an operator with repo write access can trigger a one-off build:

```bash
gh workflow run build-server-tarball.yml --repo amishagrawal2001-arch/netgen --ref vX.Y.Z
# takes ~5 min; then the .tar.gz appears on the vX.Y.Z release page
```

### Then — DPDK readiness (both paths)

After the server is running, in the client click **Tools → DPDK → Make DPDK Ready** to allocate hugepages, load vfio, build `tx_worker`, and (if needed) flip the IOMMU GRUB cmdline. Reboot if prompted.

Full details in [Step 2 below](#step-2-dpdk-readiness-one-click).

---

## License activation

<a id="license-activation"></a>

On first launch the client shows a blocking activation dialog:

- **Paste a paid license JWT** you received from your license issuer, OR
- Click **Start 30-day free trial** to unlock every feature for a month (single-use per device), OR
- Click **Buy a license** to open the purchase portal

A trial can be upgraded to a paid license at any time: **Help → License Status… → Activate License…**

The bottom-of-window pill shows the current state (✓ Licensed / ⏱ Trial · N days left / ⛔ Grace period / ⛔ Unlicensed). A top-of-window banner appears when you're ≤7 days from expiry.

Non-gated features (scapy streams, admin console, DPDK setup, RDMA setup) always work regardless of license state. Gated features: **DPDK Blast, RDMA Blast, RDMA Topology, RFC 2544**.

For headless/CI use, activate from the shell with `netgen-cli license activate --token <JWT>`.

---

## Prerequisites

| Component | Minimum |
|---|---|
| Server OS | Ubuntu 22.04 / 24.04 (others work but unsupported) |
| Server disk | 4 GB |
| Server RAM | 4 GB (8 GB recommended) |
| Server NIC | Anything with a DPDK PMD; Mellanox / Broadcom / AMD bifurcate (no bind); Intel needs vfio bind |
| Client OS | macOS 12+ / Windows 10+ / any modern Linux |
| Network | Outbound HTTPS for apt + DPDK source download (only during DPDK install) |

The tarball ships a bundled Python 3.10 venv with the wheel pre-installed. **No system pip, no PEP 668, no apt deps for Python.** The only system tools needed at install time are `bash`, `tar`, and (optionally) `docker` for the FRR / DHCP features.

---

<a id="step-2-dpdk-readiness-one-click"></a>

## Step 2: DPDK readiness (one click)

After the tarball install, the DPDK Runtime tile in the admin console will be red on a fresh box. Click **Tools → DPDK → Make DPDK Ready**. The wizard:

1. apt-installs DPDK build deps (`meson`, `ninja`, `pyelftools`, `libnuma-dev`, …)
2. Builds DPDK 23.11 from source against your kernel
3. Loads `vfio` + `vfio_pci` (persists in `/etc/modules-load.d/`)
4. Allocates 2 MiB hugepages (persists in `/etc/fstab` + sysctl)
5. Builds `tx_worker` + installs to `/usr/local/bin/tx_worker`
6. Configures IOMMU GRUB cmdline if missing → prompts reboot

Wait for green tiles. Total time on a fresh Ubuntu 24.04 box: 10–15 min (most of it `meson build`).

If you want perftest-based RDMA streams too, also run **Tools → Setup RDMA…**. Installs `rdma-core`, `perftest`, `infiniband-diags`, etc.

---

## Step 3: Client (operator laptop)

Download the matching client artifact from the same GH release:

| OS | File |
|---|---|
| macOS | `Netgen-TrafficGenerator-<v>.dmg` — drag to Applications |
| Windows | `Netgen-Client-<v>-windows.exe` — double-click |
| Linux | `Netgen-Client-<v>-linux-x86_64.AppImage` — `chmod +x` and run |

First launch → activate the license (see [above](#license-activation)) → **Tools → Add Server** → enter `http://<server>:5050` → Save. Green LED next to the server name means healthy. Add stream → Apply → Start.

---

## Upgrades

From the admin console: **Tools → Upgrade Wheel** → drag the new `.whl` in. Server restarts itself when pip is done.

From a shell on the server:

```bash
sudo netgen-upgrade /path/to/ostg_trafficgen-<new-version>-py3-none-any.whl
```

Both paths share the same state machinery (locked, persists across server restart).

---

## Uninstall

```bash
# Linux server
sudo systemctl stop netgen-server
sudo /opt/netgen-server/bin/netgen-uninstall
sudo rm -rf /opt/netgen-server
```

```bash
# macOS client
rm -rf "/Applications/Netgen Client.app"

# Linux client
rm Netgen-Client-*-linux-x86_64.AppImage
rm -rf ~/.config/netgen-client

# Windows client
# Settings → Apps → Netgen Client → Uninstall
```

---

## Auth (optional)

By default the server is open. To require a bearer token:

```bash
# In /etc/systemd/system/netgen-server.service.d/auth.conf
[Service]
Environment=NETGEN_AUTH_TOKEN=$(openssl rand -hex 32)
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart netgen-server
```

Then clients pass `Authorization: Bearer <token>` headers. Per-endpoint role gating (admin / operator / viewer) is wired in via `@require_role`.

---

## Troubleshooting

The admin console exposes everything needed to diagnose without SSH:

- **Server health** — `http://<server>:5050/admin` → top-of-page banner shows degraded state with concrete issues
- **Logs** — `GET /api/admin/journal?lines=300` or in-app **Tools → Server Journal**
- **DPDK install log** — streamed inline by the Make-Ready wizard
- **Per-iface diagnostics** — click the **ℹ️** button on any iface row for full `ethtool` dump
- **Cache inspection** — `GET /api/admin/caches` (debugging stale-data bugs)
- **Tool presence** — `GET /api/admin/health.tools_present` confirms `ip` / `ethtool` / `lldpcli` / `lspci` / `dpdk-devbind.py` are reachable

Common failure modes:

| Symptom | Likely cause | Fix |
|---|---|---|
| `netgen-install` exits early with "Docker missing" | No Docker; FRR/DHCP features won't work | Install Docker, or pass `--skip-docker` (other features still run) |
| Make-Ready hangs at "Building DPDK" | apt mirror slow; first install only | Wait — log streams live |
| Health = **degraded**, `tx_worker binary missing` | DPDK userspace installed but the `tx_worker` build step didn't run | **Tools → DPDK → Make DPDK Ready** — the wizard detects everything else already-done (green) and only runs the tx_worker build (~1 min). `$TX_WORKER_BIN` env is the escape hatch if it's built at a non-standard path. |
| Install fails with `ModuleNotFoundError: No module named 'certifi'` (or urllib3 / charset_normalizer / idna) | Wheel install path silently skipped a transitive dep, OR `pip3` and `/usr/bin/python3` point at different interpreters | Fixed in v0.5.188 installer. If stuck on older, manual unblock on target: `sudo /usr/bin/python3 -m pip install --force-reinstall certifi urllib3 charset-normalizer idna && sudo systemctl restart netgen-server`. Verify: `head -1 $(which pip3)` should match `/usr/bin/python3 --version`. |
| Fresh install via SSH runs the OLD installer even after you `wget` a newer one to `/tmp/netgen_install/` | The client SFTPs its own bundled `install_ostg_complete.py`, overwriting anything you placed there | Either upgrade the client so its bundled installer is current, OR bypass the GUI and SSH directly: `wget https://raw.githubusercontent.com/amishagrawal2001-arch/netgen/vX.Y.Z/install_ostg_complete.py && sudo python3 install_ostg_complete.py --wheel <wheel>` |
| `/api/interfaces` returns NICs with all attributes `null` (mac, driver, operstate all missing) | Server-side iface probe exceptioned silently; `psutil` or `/sys/class/net` read failed | First: `sudo systemctl restart netgen-server` and refresh the admin page. If it persists: `journalctl -u netgen-server -n 200 | grep -iE "traceback\|interface\|psutil"` — that traceback is the real bug |
| Stream starts, dies in <1s, `tx_count=0` | DPDK init failed for this stream | Journal contains the error (`[dpdk]` lines); usually iface state or PMD |
| Wrong port physically | Operator forgot which cable is which | Click **💡** button on the row → blinks the LED for 5s |
| Concurrent operators interfere | Same lab box, two browser tabs | Per-iface lock (v0.5.97) returns `409 IFACE_BUSY` — wait + retry |
| Gated menu items (DPDK Blast / RDMA / RFC 2544) greyed out | License invalid, expired, or in grace period | **Help → License Status…** — the dialog surfaces the exact reason and gives Activate / Renew buttons |

---

## Variations (less common)

**Same-host Linux turnkey** — server + client on the same Linux box:

```bash
# After server install above, also install the AppImage client
chmod +x Netgen-Client-*-linux-x86_64.AppImage
./Netgen-Client-*-linux-x86_64.AppImage -s http://localhost:5050
```

Or use the convenience script in the repo:

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git && cd netgen
sudo ./install_turnkey.sh   # builds wheel + installs server + drops desktop launcher
```

**macOS dev box** — server in Docker Desktop, client native:

Server side runs but DPDK / VRF / kernel features are unavailable. Use this for protocol development against Scapy only. See [docs/macOS.md](docs/macOS.md) if it exists; otherwise just `pip install ostg_trafficgen-*.whl` into a venv and run `ostg-server`.

**WSL2 on Windows** — server inside WSL2, client native Windows:

WSL2 sees host network adapters via virtio bridge — DPDK works but performance is bridge-bound. Same install commands as Linux server, executed inside the WSL2 distro.

**Building from source** — for developers patching netgen itself:

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git && cd netgen
./rebuild_quick.sh                   # builds the wheel
sudo ./install_turnkey.sh            # full local install
```

---

## Advanced / workarounds

### Force a specific installer version (bypass the bundled one)

If your client is older than the fix you need, don't fight the SFTP overwrite — install directly on the target:

```bash
cd /tmp && sudo rm -rf /tmp/netgen_install && mkdir -p /tmp/netgen_install && cd /tmp/netgen_install

# Pick any tag that has the fix
wget https://raw.githubusercontent.com/amishagrawal2001-arch/netgen/vX.Y.Z/install_ostg_complete.py

# Any current wheel works — the installer only reads pyproject metadata for logging
wget https://github.com/amishagrawal2001-arch/netgen/releases/download/vX.Y.Z/ostg_trafficgen-X.Y.Z-py3-none-any.whl

sudo /usr/bin/python3 install_ostg_complete.py --wheel ostg_trafficgen-*.whl
```

### Build a tarball for a tag that's missing one

Between v0.5.22 and v0.5.186 the tarball workflow didn't auto-fire on tags. To backfill:

```bash
gh workflow run build-server-tarball.yml --repo amishagrawal2001-arch/netgen --ref vX.Y.Z
# Watch it:
gh run list --repo amishagrawal2001-arch/netgen --workflow build-server-tarball.yml --limit 3
```

~5 min. The `.tar.gz` appears on the `vX.Y.Z` release page when done.

### Headless license activation

```bash
netgen-cli license fingerprint                           # send this to your license issuer
netgen-cli license activate --token '<JWT>'              # or --file <path>
netgen-cli license status                                # confirm
netgen-cli license trial                                 # start 30-day trial instead
netgen-cli license deactivate                            # remove
```

Alternate: set `NETGEN_LICENSE_TOKEN=<JWT>` in the environment (kiosk / CI use) — it overrides `~/.netgen/license.jwt`. `NETGEN_LICENSE_FILE=<path>` also works.

---

## See also

- **In-app help** — Help → Installation Guide (this same content, rendered in the client)
- **CHANGELOG** — `CHANGELOG.md` in the repo
- **API reference** — `http://<server>:5050/admin/api-guide`
