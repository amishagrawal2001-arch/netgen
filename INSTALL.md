# Netgen Install Guide

Two install methods, six deployment scenarios.

## Option A — Pre-built installer (fastest)

Download from the latest **[GitHub release](https://github.com/amishagrawal2001-arch/netgen/releases/latest)**.
Every tagged release ships four artifacts built by CI:

| File | Use for |
|------|---------|
| `Netgen-Client-<v>-windows.exe`           | Windows client, no Python install needed |
| `Netgen-TrafficGenerator-<v>.dmg`         | macOS client, drag-to-Applications |
| `Netgen-Client-<v>-linux-x86_64.AppImage` | Linux client, single-file, works on any modern distro |
| `ostg_trafficgen-<v>-py3-none-any.whl`    | Universal wheel — for the server install + scripted client installs |

### Quick install per platform (Option A)

**Windows**:  download the `.exe` → double-click → done. The bundled
Python + PyQt5 + all deps make it a single-file install.

**macOS**:  download the `.dmg` → mount → drag **Netgen Client.app**
to `Applications` → launch from Spotlight or Launchpad.

**Linux** (any modern distro):
```bash
chmod +x Netgen-Client-*-linux-x86_64.AppImage
./Netgen-Client-*-linux-x86_64.AppImage -s http://lab-box:5050
```

**Server** (Linux box that owns the NICs):
```bash
# Download the wheel + the repo's install_ostg_complete.py
wget https://github.com/amishagrawal2001-arch/netgen/releases/latest/download/ostg_trafficgen-VERSION-py3-none-any.whl
git clone --depth 1 https://github.com/amishagrawal2001-arch/netgen.git netgen-installer
sudo python3 netgen-installer/install_ostg_complete.py
```

That's it for Option A. For finer control or to customize before shipping,
see Option B below.

---

## Option B — Build from source (full control)

Pick the row that matches your deployment and follow the linked steps.

## Quick chooser

| # | Profile | Server runs on | Client runs on | Status |
|---|---------|----------------|----------------|--------|
| 1 | **Turnkey** | Linux         | Linux (same host) | ✅ Full — `install_turnkey.sh` |
| 2 | **Turnkey** | macOS         | macOS (same host) | ⚠️ Limited — Docker Desktop, scapy fallback, no DPDK / VRF |
| 3 | **Turnkey** | WSL2 Linux    | Windows (same host) | ⚠️ Hybrid — server lives inside WSL2 |
| 4 | **Split**   | Linux server  | Linux laptop      | ✅ Full — `install_ostg_complete.py` + `install_client.sh` |
| 5 | **Split**   | Linux server  | macOS laptop      | ✅ Full — `install_ostg_complete.py` + `install_client.sh` |
| 6 | **Split**   | Linux server  | Windows laptop    | ✅ Full — `install_ostg_complete.py` + `install_client.ps1` / `install_client.bat` |

**The server is fundamentally a Linux workload.** It depends on:
- Per-device Linux VRFs (kernel feature)
- DPDK (Linux kernel modules + hugepages + Mellanox/Intel drivers)
- `iproute2` for VLAN subinterfaces and VXLAN tunnels
- systemd for service management
- FRR Docker containers (works on Docker Desktop too, but cross-platform is slower)

The **client** is plain Python + PyQt5 and runs everywhere.

## Prerequisites

| Component | Minimum |
|---|---|
| Python (server) | 3.10 (matches Ubuntu 22.04 default) |
| Python (client) | 3.9 |
| OS (server, native) | Ubuntu 22.04 / Debian 12 / RHEL 9 / Rocky 9 / Fedora 38+ |
| OS (server, Docker Desktop fallback) | macOS 12+ or Windows 10 / 11 with WSL2 |
| Disk | 4 GB (server with FRR image + DPDK) / 500 MB (client) |
| RAM | 4 GB minimum / 8 GB recommended |
| Network | Outbound HTTPS to PyPI for dependency install |

---

## 1. Turnkey Linux (server + GUI on one Linux host)

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh                  # build the wheel
sudo ./install_turnkey.sh           # full install + desktop launcher
```

`install_turnkey.sh` runs `install_ostg_complete.py` (Python venv,
Docker, FRR image, DPDK, systemd unit) and then drops an XDG desktop
entry for `ostg-client` pointed at `localhost:5050`.

After install:
- Server: `systemctl status netgen-server`
- GUI: Applications menu → Network → Netgen Client (or `ostg-client`)

Flags:
```bash
sudo ./install_turnkey.sh --no-dpdk            # devbox without a DPDK NIC
sudo ./install_turnkey.sh --skip-dpdk-build    # apt deps only, no DPDK compile
```

---

## 2. Turnkey macOS (limited — same host, Docker Desktop)

⚠️ **Use case**: developer running Netgen against itself for protocol
correctness tests. **Not** a production setup — macOS has no DPDK and
no Linux VRFs, so line-rate generation and per-device isolation
aren't available.

What works:
- The full GUI (PyQt5 is native on macOS)
- FRR Docker containers via Docker Desktop
- Scapy-based packet generation at moderate pps (no DPDK)
- Stateful TCP, DNS, SIP, HTTP at full L7 fidelity
- BGP / OSPF / IS-IS control plane via FRR (single-instance, no per-device VRF)
- L2 frame generators (LACP / LLDP / VRRP / IGMP / PIM) need root on
  macOS for raw sockets

What doesn't work:
- DPDK line-rate streams (Linux-only kernel modules)
- Per-device VRF isolation (kernel-feature absent)
- Multi-device scale tests (no VRF means one BGP daemon per port max)

Install (Docker Desktop must be installed and running first):

```bash
brew install python@3.12 docker
open -a "Docker Desktop"   # wait for whale icon to settle

git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh
sudo python3 install_ostg_complete.py --no-dpdk

# Launch server manually (no systemd on macOS):
ostg-server &

# Launch GUI:
ostg-client -s http://127.0.0.1:5050
```

L2 frame generators need root + scapy's BPF access on macOS — if you
see `PermissionError` in the L2 Emulation tab's Last Error column,
either run the server as root or follow scapy's macOS BPF setup guide.

---

## 3. Turnkey Windows (WSL2 + GUI on Windows host)

⚠️ **The server inside WSL2 IS a Linux server.** This is "Turnkey
Linux" with the Linux running inside WSL2 — but the GUI side is
Windows-native.

Steps:

```powershell
# 1. Enable WSL2 + install Ubuntu 22.04
wsl --install -d Ubuntu-22.04
# (reboot if first time)

# 2. Inside WSL2 — same as Turnkey Linux
wsl -d Ubuntu-22.04
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh
sudo ./install_turnkey.sh --no-dpdk

# DPDK in WSL2 needs DPDK + hugepages + NIC passthrough config —
# usually not worth it for WSL2; --no-dpdk is the sensible default.

# Verify from WSL2 shell:
curl http://127.0.0.1:5050/api/health
```

Then install the client **on Windows** (not inside WSL2) so the GUI
runs as a native Windows app talking to the WSL2 server:

```powershell
# Back on Windows:
cd path\to\netgen
.\install_client.bat -Server http://localhost:5050
```

(WSL2 forwards `localhost:5050` to the Linux host inside WSL by
default. If you've changed networking modes, use the WSL2 IP from
`wsl hostname -I`.)

---

## 4. Split — Linux server + Linux laptop client

### On the Linux server (one-time setup):

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh
sudo python3 install_ostg_complete.py
```

Or from your laptop, push the install:
```bash
python3 install_ostg_complete.py -H lab-box -u root -p password
```

The script copies the wheel, installs everything, builds the FRR
Docker image, sets up DPDK, and starts `netgen-server.service`.

### On your Linux laptop:

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh
./install_client.sh -s http://lab-box:5050
```

`install_client.sh` builds a per-user venv at `~/.netgen-client` (no
root) and drops `~/.local/share/applications/netgen-client.desktop`
for the GNOME / KDE / XFCE app menu.

---

## 5. Split — Linux server + macOS laptop client

### Server: same as scenario #4.

### Client (macOS):

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh
./install_client.sh -s http://lab-box:5050
```

Drops `~/Applications/Netgen Client.command` — double-click from
Finder to launch.

**Alternative for sharing with non-dev users**: build a `.dmg`:

```bash
./build_dmg.sh                   # quick DMG with .app inside
./build_macos_installer.sh       # full installer DMG with wheel + docs
```

Output lands in `build_image/Netgen-TrafficGenerator-<version>.dmg`.
Ship the DMG to your users — they drag the .app into Applications and
launch it normally.

---

## 6. Split — Linux server + Windows laptop client

### Server: same as scenario #4.

### Client (Windows 10 / 11):

Prerequisites: Python 3.9+ on PATH (install from
<https://www.python.org/downloads/windows/>, check **"Add python.exe
to PATH"** during install).

**Option A: PowerShell (recommended)**
```powershell
cd path\to\netgen
.\install_client.ps1 -Server http://lab-box:5050
```

If you hit "execution of scripts is disabled":
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Option B: Double-click the .bat (no PowerShell knowledge needed)**

`install_client.bat` wraps the .ps1 with `-ExecutionPolicy Bypass`.
Edit the `.bat` once to bake in your server URL, then double-click it
from Explorer.

The installer drops both **Desktop** and **Start Menu** shortcuts
named "Netgen Client". Double-click to launch the GUI.

---

## Updating an existing install

### From the GUI (operators) — recommended

**Help → Install / Upgrade Server… → Upgrade running server** tab. Enter
the server URL + pick the new `.whl`, then click one of:

- **Upload && Upgrade** — HTTP path (`POST /api/admin/upgrade_wheel`); the
  server pip-installs the wheel and restarts itself. Auto-falls back to
  SSH if the endpoint is missing (HTTP 404) or erroring, provided you've
  filled in the SSH credentials.
- **Upgrade via SSH (manual)** — skips HTTP entirely: sftp the wheel,
  `pip install --upgrade --force-reinstall --no-deps`, restart the
  service. Use this for **old servers** that predate the
  `/api/admin/upgrade_wheel` endpoint (pre-0.2.6), or whenever you prefer
  the direct path. The restart tries `netgen-server` and falls back to the
  legacy `ostg-server` unit.

Manual one-liner equivalent (terminal):

```bash
scp ostg_trafficgen-<ver>-py3-none-any.whl root@<host>:/tmp/
ssh root@<host> 'pip3 install --upgrade --force-reinstall --no-deps \
    /tmp/ostg_trafficgen-<ver>-py3-none-any.whl && \
    (systemctl restart netgen-server || systemctl restart ostg-server)'
```

On 0.2.28+, the server's startup self-heal redeploys the FRR/DHCP assets
to `/opt/netgen/` and rebuilds the container image automatically when the
bundled `Dockerfile.frr` changes — so a wheel-only upgrade is self-sufficient.

### From the repo scripts (developers)

Bump the version in `pyproject.toml`, then on each host:

```bash
# Linux server (or from a laptop via -H):
./rebuild_quick.sh
SERVER_HOST=lab-box ./deploy.sh -t wheel-only

# Linux / macOS client:
./install_client.sh --upgrade

# Windows client:
.\install_client.ps1 -Upgrade
```

Every build script auto-detects the version from `pyproject.toml` —
you bump it in one place and all five (`deploy.sh`, `rebuild_quick.sh`,
`rebuild_wheel.sh`, `build_dmg.sh`, `build_macos_installer.sh`,
`install_ostg_complete.py`) pick it up automatically. Same for the
client installers (`install_client.sh` and `install_client.ps1`) —
they glob the freshest wheel from `dist/`.

---

## Uninstall

### Linux server
```bash
sudo systemctl stop netgen-server
sudo systemctl disable netgen-server
sudo rm /etc/systemd/system/netgen-server.service
sudo rm -rf /opt/netgen
sudo docker rmi netgen-frr:latest
```

### Linux / macOS client
```bash
rm -rf ~/.netgen-client
rm -f ~/.local/share/applications/netgen-client.desktop   # Linux
rm -f ~/Applications/Netgen\ Client.command               # macOS
```

### Windows client
```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.netgen-client"
Remove-Item "$env:USERPROFILE\Desktop\Netgen Client.lnk" -ErrorAction SilentlyContinue
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Netgen Client.lnk" -ErrorAction SilentlyContinue
```

---

## Authentication

The server's auth is opt-in. Two modes:

**Single token** (back-compat):
```bash
# On the server:
export NETGEN_AUTH_TOKEN=your-secret
# Or via systemd:
sudo systemctl edit netgen-server
# add: Environment=NETGEN_AUTH_TOKEN=your-secret
sudo systemctl restart netgen-server

# On the client (any platform):
export NETGEN_AUTH_TOKEN=your-secret   # macOS / Linux
$env:NETGEN_AUTH_TOKEN="your-secret"   # Windows PowerShell
setx NETGEN_AUTH_TOKEN your-secret     # Windows persistent
```

**Per-role tokens** (`viewer` / `operator` / `admin` hierarchy):
```bash
# On the server:
export NETGEN_AUTH_TOKENS_JSON='{"abc...":"admin","def...":"operator","ghi...":"viewer"}'
```

The GUI client and `netgen-cli` auto-inject the header from
`$NETGEN_AUTH_TOKEN`. `/api/health` is always exempt so probes don't
need credentials.

See **API_GUIDE.md** § Authentication for the full role hierarchy
and endpoint matrix.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `pip install` fails with PyQt5 errors on macOS | Building from source instead of using prebuilt wheels | `pip install --find-links https://download.qt.io/snapshots/ci/pyqt5/5.15/wheels/ PyQt5` |
| `Could not find the Qt platform plugin 'cocoa'` | PyQt5 installed from source | Same fix as above |
| GUI starts but stays grey/blank | X server not available (Linux / WSL2) | Install Xorg / use VcXsrv on WSL2 / consider WSLg on Windows 11 |
| `/api/l2/sessions` returns 404 | Server is older than 0.2.4 | `git pull` + `SERVER_HOST=<host> ./deploy.sh -t wheel-only` |
| L2 Emulation "Permission Error" in Last Error | scapy needs raw sockets | Linux: `setcap cap_net_raw=eip $(readlink -f $(which python3))` then restart server. macOS: run server as root. Windows: install Npcap and run client as Administrator. |
| `ostg-client` not found on PATH (Linux) | Venv not activated, or pip installed without entry-point support | `source ~/.netgen-client/bin/activate` first; or reinstall with `--upgrade` |
| Wheel build fails with `error: invalid command 'bdist_wheel'` | `wheel` package missing | `pip install build wheel` |
| Install hangs at "Building Docker FRR image" | First-time FRR build pulling base images | Be patient — first build is 5-10 min. `docker logs $(docker ps -lq)` from another shell shows progress. |
| `python3 install_ostg_complete.py` complains version mismatch | Stale `egg-info/` cache from old build | `rm -rf ostg_trafficgen.egg-info build dist && ./rebuild_quick.sh` |

For anything else, the server logs are the first place to look:
```bash
journalctl -u netgen-server -n 100 --no-pager   # Linux systemd
# Or directly: tail -f /var/log/netgen/server.log
```

GUI client logs go to stderr — launch from a terminal to see them:
```bash
ostg-client -s http://lab-box:5050   # Linux / macOS
# Or on Windows:
& "$env:USERPROFILE\.netgen-client\Scripts\ostg-client.exe"
```

---

## Building installers from source

If you're cutting a customer-shippable release locally (or want to
build a variant the GitHub Actions CI doesn't produce), each platform
has its own build script.

| Platform | Build script | Output |
|---|---|---|
| **Windows** (PyInstaller .exe) | `.\build_windows.ps1`               | `dist\Netgen-Client-<v>-windows.exe`               |
| **macOS** (drag-to-Applications .dmg) | `./build_dmg.sh`            | `build_image/Netgen-TrafficGenerator-<v>.dmg`      |
| **macOS** (full installer DMG) | `./build_macos_installer.sh`         | `build_image/Netgen-TrafficGenerator-<v>-macOS.dmg` (includes wheel + docs) |
| **Linux** (universal AppImage) | `./build_appimage.sh`                | `dist/Netgen-Client-<v>-linux-<arch>.AppImage`     |

All build scripts auto-detect the version from `pyproject.toml`. Bump
the version once and every script re-builds correctly.

Constraints:
- **PyInstaller is OS-native** — no cross-builds. To produce a
  Windows .exe you need a Windows build host. Same for macOS → .dmg
  and Linux → .AppImage. The GitHub Actions workflow handles this by
  matrix-running across `windows-latest`, `macos-latest`, and
  `ubuntu-latest`; trigger it with a `git tag v<x.y.z> && git push --tags`.
- **AppImage requires FUSE on the build host** (`apt install fuse libfuse2`).
- **Code-signing is not currently wired up** — macOS users will see
  the "unidentified developer" warning on first launch (right-click →
  Open). Windows users get a SmartScreen warning. Signing identities
  can be added later via env-var secrets in the GitHub Actions
  workflow.

### Continuous-integration release flow

`.github/workflows/release.yml` runs the full build matrix on every
tag push:

```bash
# Bump pyproject.toml, then:
git tag -a v0.2.5 -m "v0.2.5"
git push target v0.2.5
```

The workflow:
1. Builds the universal wheel on Ubuntu
2. Builds the .dmg on macOS
3. Builds the .exe on Windows
4. Builds the .AppImage on Ubuntu
5. Pulls release notes from `CHANGELOG.md`
6. Creates the GitHub release with all four artifacts attached

Customers can `wget` the platform installer they want straight from
the release page.
