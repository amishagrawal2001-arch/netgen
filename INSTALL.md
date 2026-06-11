# Netgen Install Guide

## Quickstart — pick one

### Path A — Install from the client (easiest, no SSH typing)

If you have a Linux box you can SSH into + your operator laptop, the client can drive the whole install for you:

1. **Install the client** on your laptop — download from the latest GH release:
   - macOS — `Netgen-TrafficGenerator-<v>.dmg` (drag to Applications)
   - Windows — `Netgen-Client-<v>-windows.exe` (double-click)
   - Linux — `Netgen-Client-<v>-linux-x86_64.AppImage` (`chmod +x` + run)
2. **Launch the client** and open **Help → Install / Upgrade Server…**
3. Switch to the **Fresh install via SSH** tab
4. Enter the target Linux host + SSH user + key/password
5. Click **Install**. The dialog SFTPs the bundled tarball assets to the host, runs `netgen-install` over SSH, and streams the live log inline (with error extraction so failures don't make you scroll a 1000-line log)
6. When done, **Tools → Add TGen Chassis** → enter the server URL → green LED = ready

The wheel + tarball are bundled inside every client artifact, so step 1 covers everything — no separate download.

### Path B — Direct on the Linux server (no laptop needed)

```bash
VER=$(curl -s https://api.github.com/repos/amishagrawal2001-arch/netgen/releases/latest \
       | grep -oP '"tag_name": "v\K[^"]+')
wget https://github.com/amishagrawal2001-arch/netgen/releases/latest/download/netgen-server-${VER}.tar.gz
sudo mkdir -p /opt/netgen-server && \
  sudo tar -xzf netgen-server-*.tar.gz -C /opt/netgen-server --strip-components=1 && \
  sudo /opt/netgen-server/bin/netgen-install
```

Verify:

```bash
systemctl status netgen-server
curl -s http://localhost:5050/api/admin/health | jq .health   # "healthy"
```

### Then — DPDK readiness (both paths)

After the server is running, browse to `http://<server>:5050/admin` and click **Tools → DPDK → Make DPDK Ready** to allocate hugepages, load vfio, build `tx_worker`, and (if needed) flip the IOMMU GRUB cmdline. Reboot if prompted.

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

First launch → **Tools → Add TGen Chassis** → enter `http://<server>:5050` → Save. Green LED next to the server name means healthy. Add stream → Apply → Start.

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
| `tx_worker binary` tile shows red | install_dpdk.sh didn't finish, or wrong path | Tile tooltip names the fix; `$TX_WORKER_BIN` env override is the escape hatch |
| Stream starts, dies in <1s, `tx_count=0` | DPDK init failed for this stream | Journal contains the error (`[dpdk]` lines); usually iface state or PMD |
| Wrong port physically | Operator forgot which cable is which | Click **💡** button on the row → blinks the LED for 5s |
| Concurrent operators interfere | Same lab box, two browser tabs | Per-iface lock (v0.5.97) returns `409 IFACE_BUSY` — wait + retry |

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

## See also

- **In-app help** — Help → Installation Guide (this same content, rendered in the client)
- **CHANGELOG** — `CHANGELOG.md` in the repo
- **API reference** — `http://<server>:5050/admin/api-guide`
