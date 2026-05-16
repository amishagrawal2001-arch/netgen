# OSTG / Netgen (Open Source Traffic Generator)

A comprehensive network traffic generation and device management system with support for various protocols including BGP, OSPF, IS-IS, and advanced traffic patterns.

## What's New in 0.2.5

See [CHANGELOG.md](CHANGELOG.md) for the full diff. Highlights:

- **Prebuilt installers** — every tagged release on GitHub now ships
  four artifacts built by CI: `.exe` (Windows client), `.dmg` (macOS
  client), `.AppImage` (Linux client), and the universal `.whl`
  (server + scripted client installs). Operators no longer need a
  Python toolchain to run the GUI. See [INSTALL.md](INSTALL.md)
  Option A.
- **Windows install path** — `install_client.ps1` / `install_client.bat`
  parallel to `install_client.sh`: per-user venv, Desktop + Start
  Menu shortcuts, no admin required.
- **Per-device VRF isolation** — every managed FRR Docker container
  sits in its own Linux VRF (`vrf-<short>`) so 10s of emulated devices
  on one host don't share a routing table.
- **Topology tab** — IXNetwork-style canvas: port lane at the bottom,
  device cards with vertical protocol-stack chips
  (ETH / IPv4 / IPv6 / BGP / OSPF / ISIS / DHCP), status LEDs, cables,
  and a right-side property panel. See [Topology Tab](#topology-tab).
- **Stateful TCP** — real-socket parallel to the scapy stateless
  generator: actual 3-way handshakes, TLS, HTTP/1.1 framing, Linux
  VRF binding (`SO_BINDTODEVICE`), and `TCP_INFO` retransmit / RTT
  scraping. `/api/stateful_tcp/*` and `netgen-cli tcp`.
  See [Stateful TCP](#stateful-tcp).
- **State-history timeline** — every protocol monitor writes a row
  on each observed state transition. `Ctrl+H` in the GUI shows a
  per-protocol timeline; `/api/device/database/devices/<id>/history[/<proto>]`
  exposes it programmatically.
- **View Device Config** — `Ctrl+J` opens a read-only JSON viewer
  for the selected device's full server-side config, copy-to-clipboard.
- **Bearer-token auth** — opt-in via `NETGEN_AUTH_TOKEN`. `/api/health`
  stays exempt so k8s/HAProxy probes work without credentials.
- **`netgen-cli`** — headless companion: `health`, `list`, `export`,
  `import`, `apply`, `status`, `wait`, plus full `tcp <subcommand>`
  for stateful sessions.
- **GUI quality-of-life** — Apply progress bar, monitor-health
  indicator, filter bar, Retry Failed Apply, Settings dialog, and a
  complete keyboard-shortcut set (`Ctrl+Return / S / X / R / F / H / J`).
- **36 pytest cases** covering VRF naming, stateful-TCP loopback
  echo / TLS / HTTP framing / VRF degrade / TCP_INFO degrade /
  dead-target, plus all legacy helpers.

## Table of Contents

- [Installation](#installation)
- [Build Scripts](#build-scripts)
- [Deployment Scripts](#deployment-scripts)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Client-Server Communication](#client-server-communication)
- [Device Management](#device-management)
- [Topology Tab](#topology-tab)
- [Stateful TCP](#stateful-tcp)
- [netgen-cli (Headless CLI)](#netgen-cli-headless-cli)
- [Traffic Generation API](#traffic-generation-api)
- [DPDK Multi-Queue Scaling](#dpdk-multi-queue-scaling)
  - [Installing DPDK on the Linux server](#installing-dpdk-on-the-linux-server)
- [Protocol Configuration](#protocol-configuration)
- [Monitoring and Troubleshooting](#monitoring-and-troubleshooting)
  - [Server Admin Portal (`/admin`)](#server-admin-portal-admin)
  - [Consolidated health JSON](#consolidated-health-json)
  - [systemd + journalctl](#systemd--journalctl)
  - [Health probe + SSE event stream](#health-probe--sse-event-stream)
- [Examples](#examples)

## Installation

For the complete install matrix (all six profiles: turnkey / split ×
Linux / macOS / Windows) see [INSTALL.md](INSTALL.md). Quick
chooser below for the three most common paths:

### 0. Just the client, no Python toolchain (download prebuilt)

Operators who only need the GUI can grab a prebuilt installer from
the **[latest GitHub release](https://github.com/amishagrawal2001-arch/netgen/releases/latest)**:

| File | Platform |
|------|----------|
| `Netgen-Client-<v>-windows.exe`           | Windows |
| `Netgen-TrafficGenerator-<v>.dmg`         | macOS — drag-to-Applications |
| `Netgen-Client-<v>-linux-x86_64.AppImage` | Linux — `chmod +x` then run |
| `ostg_trafficgen-<v>-py3-none-any.whl`    | Universal — for the server install |

The server still has to run on Linux (see option A or B below); these
are client-only bundles.

### A. Turnkey lab-in-a-box (server + GUI on one Linux host)

Single command. Installs the wheel, builds the FRR Docker image,
sets up DPDK + systemd, drops a desktop launcher for the GUI.

```bash
git clone <repository-url>
cd netgen
./rebuild_quick.sh                      # build the wheel (auto-versioned)
sudo ./install_turnkey.sh               # full install + desktop launcher
```

After this:
- Server runs as `systemctl status netgen-server`.
- GUI launches from `Applications → Network → Netgen Client` or via
  `ostg-client -s http://127.0.0.1:5050`.

Flags pass through to the underlying server install:
```bash
sudo ./install_turnkey.sh --no-dpdk          # devbox with no DPDK NIC
sudo ./install_turnkey.sh --skip-dpdk-build  # apt deps only
```

### B. Typical split (Linux server + operator laptop)

**On the lab server** (one-time setup):
```bash
git clone <repository-url>
cd netgen
./rebuild_quick.sh
sudo python3 install_ostg_complete.py
# Or remote install from your laptop:
python3 install_ostg_complete.py -H lab-box -u root -p password
```

**On your laptop** (or any operator machine):
```bash
./install_client.sh                                  # uses dist/*.whl
./install_client.sh -s http://lab-box:5050           # pre-set server URL
./install_client.sh path/to/ostg_trafficgen-*.whl    # explicit wheel
./install_client.sh --upgrade                        # force re-install
```

`install_client.sh` auto-detects macOS / Linux / WSL, builds a per-user
venv under `~/.netgen-client`, and drops a desktop launcher / .command
wrapper. No root needed.

### Updating an existing install

```bash
# Bump pyproject.toml version, then:
./rebuild_quick.sh

# Server side:
SERVER_HOST=lab-box ./deploy.sh -t wheel-only

# Client side:
./install_client.sh --upgrade
```

Both paths auto-detect the wheel version now — no more
`hardcoded-0.1.52-doesn't-match-actual-build` confusion.

### macOS .dmg distribution (for shipping to customers)

```bash
./build_dmg.sh                       # Quick DMG (apps only)
./build_macos_installer.sh           # Full installer (apps + wheel + docs)
```

Both auto-version from `pyproject.toml`. Output lands in `build_image/`.

### Development Environment Setup

For local dev work without the install scripts:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
pip install -r requirements.txt
```

Run `ostg-server` and `ostg-client` from the venv. `pytest -q tests/`
runs the test suite.

**Note**: PyQt5 on macOS is sensitive — if you hit "Could not find
the Qt platform plugin 'cocoa'", install from Qt's official wheels:
```bash
pip install --find-links https://download.qt.io/snapshots/ci/pyqt5/5.15/wheels/ PyQt5
```

## Script Overview

OSTG provides a comprehensive set of scripts for building, deploying, and installing the traffic generator:

### 🏗️ Build Scripts
- **`rebuild_quick.sh`** - Fast wheel package build for development
- **`rebuild_wheel.sh`** - Comprehensive wheel package build for production
- **`build_dmg.sh`** - macOS DMG installer (client-only; server is Linux-only)
- **`build_macos_installer.sh`** - macOS DMG installer (alternate full-package flow)
- **`build_windows.ps1`** - Windows `.exe` builder (PyInstaller one-file)
- **`build_appimage.sh`** - Linux `.AppImage` builder (single-file portable)

### 🚀 Deployment Scripts
- **`deploy.sh`** - Flexible deployment to remote servers
- **`deploy_quick.sh`** - Interactive deployment wrapper

### 🏗️ Installation Scripts
- **`install_turnkey.sh`** — Single-host install (server + GUI on the same Linux box). Wraps the complete installer + adds a desktop launcher.
- **`install_client.sh`** — Client-only install (operator laptop, no Docker/DPDK). Detects macOS / Linux / WSL, builds a per-user venv under `~/.netgen-client`.
- **`install_client.ps1`** / **`install_client.bat`** — Windows client install (PowerShell + double-click wrapper). Per-user venv at `%USERPROFILE%\.netgen-client`, Desktop + Start Menu shortcuts.
- **`install_ostg_complete.py`** — Lab-server install (full Docker + FRR + DPDK + systemd). Local or remote via `-H`.

### 🤖 CI / Release
- **`.github/workflows/release.yml`** — On every `v*` tag, builds the wheel + macOS `.dmg` + Windows `.exe` + Linux `.AppImage` in parallel and attaches them to a GitHub release. Release notes are extracted from `CHANGELOG.md`.

### 📊 Script Workflow

```
Source Code Changes
        ↓
   Platform Choice?
   ↙        ↘
Linux/Remote    macOS
    ↓            ↓
rebuild_quick   build_dmg.sh
rebuild_wheel   build_macos_installer.sh
    ↓            ↓
 deploy.sh    DMG Distribution
    ↓
install_ostg_complete.py
```

This will install:
- **OSTG Traffic Generator** (complete server and client)
- **Python 3.9+** with all build dependencies
- **Docker Engine** with networking support
- **FRR Docker containers** for BGP/OSPF routing
- **PyQt5 GUI framework** for client interface
- **Network analysis tools** (nmap, netcat, socat, bridge-utils, vlan)
- **System monitoring tools** (iotop, nethogs, iftop, htop)
- **Development tools** (vim, nano, git, jq, yq)
- **Systemd services** for automatic startup
- **All Python dependencies** and build tools
- **Network utilities** (traceroute, mtr)
- **Security tools** (SSH client/server)
- **Archive tools** (zip, unzip, tar, gzip)

### Installation Options

#### Local Installation
```bash
# Install to default directory /opt/OSTG
sudo python3 install_ostg_complete.py

# Install to custom directory
sudo python3 install_ostg_complete.py -d /custom/path

# Use custom wheel source directory
python3 install_ostg_complete.py -w /path/to/wheels
```

#### Remote Installation
```bash
# Install to remote server
python3 install_ostg_complete.py -H server.com -u admin -p password

# Install to specific IP with custom directory
python3 install_ostg_complete.py -H 192.168.1.100 -u root -p secret -d /opt/OSTG
```

#### Environment Variables
```bash
export SERVER_HOST="server.com"
export SERVER_USER="admin"
export SERVER_PASS="password"
export INSTALL_DIR="/opt/OSTG"
export WHEEL_SOURCE_DIR="dist"
python3 install_ostg_complete.py
```

### System Requirements

#### **Supported Operating Systems:**
- **Ubuntu/Debian** (18.04+, 20.04+, 22.04+)
- **CentOS/RHEL** (7+, 8+, 9+)
- **Alpine Linux** (3.15+)
- **openSUSE** (Leap 15+, Tumbleweed)
- **Fedora** (35+)

#### **Minimum System Requirements:**
- **CPU:** 2 cores, 2.0 GHz
- **RAM:** 4 GB (8 GB recommended for production)
- **Storage:** 10 GB free space
- **Network:** Ethernet interface(s) for traffic generation
- **Root privileges** for Docker and network configuration

#### **Automatically Installed Dependencies:**
- **Python 3.9+** (installed if not present)
- **Docker Engine** (installed and configured)
- **PyQt5 GUI Framework** (for client interface)
- **Network Analysis Tools** (nmap, netcat, socat, etc.)
- **System Monitoring Tools** (iotop, nethogs, iftop, htop)
- **Development Tools** (vim, nano, git, jq, yq)
- **Build Dependencies** (gcc, g++, make, pkg-config)
- **Python Build Tools** (cython, numpy, cffi)

#### **Network Requirements:**
- Internet connectivity for package downloads
- Network interfaces for traffic generation
- Port 5051 available for OSTG server
- Docker networking support

### Manual Installation

For detailed manual installation steps, see [INSTALLATION.md](INSTALLATION.md).

## Build Scripts

OSTG provides two build scripts for different use cases:

### Quick Rebuild (`rebuild_quick.sh`)

Fast rebuild script for development cycles:

```bash
# Quick rebuild for development
./rebuild_quick.sh
```

**Features:**
- Fast execution (~5-10 seconds)
- Basic cleanup (build/, dist/, *.egg-info/)
- Simple wheel build
- Copy to root directory
- Minimal output

**Use Cases:**
- Development iterations
- Quick testing
- Fast feedback cycles

### Comprehensive Rebuild (`rebuild_wheel.sh`)

Thorough rebuild script for production builds:

```bash
# Comprehensive rebuild with validation
./rebuild_wheel.sh
```

**Features:**
- Extensive cleanup (including .pyc files, __pycache__)
- BGP timer fix verification
- Dependency installation and updates
- Project structure validation
- Build verification and testing
- Installation testing
- Command line tool verification
- Backup creation
- Deployment information generation
- Comprehensive logging

**Use Cases:**
- Production builds
- Release preparation
- CI/CD pipelines
- Thorough validation

### Script Comparison

| Feature | `rebuild_quick.sh` | `rebuild_wheel.sh` | `install_ostg_complete.py` |
|---------|-------------------|-------------------|---------------------------|
| **Purpose** | 🏗️ **Build** wheel (fast) | 🏗️ **Build** wheel (thorough) | 🚀 **Install** system |
| **Speed** | Fast (~5-10s) | Thorough (~30-60s) | Complete (~5-15min) |
| **Input** | Source code | Source code | Wheel file + system |
| **Output** | `.whl` file | `.whl` file | Installed OSTG |
| **Validation** | Basic | Extensive | System verification |
| **Testing** | None | Full installation test | Service verification |
| **BGP Verification** | None | Yes | Runtime testing |
| **Dependencies** | Python, pip, build tools | Python, pip, build tools | System packages, Docker |
| **Target Machine** | Development | Development | Production server |
| **Frequency** | After code changes | Before releases | First-time setup |
| **Use Case** | Development cycles | Production builds | System installation |
| **File Size** | ~585KB wheel | ~585KB wheel | Full system install |
| **Cleanup** | Basic | Extensive | None |

### When to Use Each Script

#### 🔄 Development Workflow
```bash
# 1. Make code changes
# 2. Quick rebuild for testing
./rebuild_quick.sh

# 3. Quick deployment to dev server
./deploy.sh -t wheel-only
```

#### 🚀 Production Release
```bash
# 1. Comprehensive rebuild with validation
./rebuild_wheel.sh

# 2. Full deployment to production
./deploy.sh -t full
```

#### 🏗️ First-Time Installation
```bash
# 1. Build the wheel package (if not done)
./rebuild_wheel.sh

# 2. Install on target system
python3 install_ostg_complete.py
```

## macOS Build Scripts

OSTG provides two macOS build scripts for creating standalone applications:

### Simple DMG Builder (`build_dmg.sh`)

Creates a lightweight DMG installer with just the applications:

```bash
# Build simple DMG installer
./build_dmg.sh
```

**Features:**
- ✅ **Fast build** (~2-3 minutes)
- ✅ **Lightweight** (~55MB DMG)
- ✅ **Self-contained** apps
- ✅ **No dependencies** required

### Complete Installer Builder (`build_macos_installer.sh`)

Creates a comprehensive DMG installer with full documentation and scripts:

```bash
# Build complete installer
./build_macos_installer.sh
```

**Features:**
- ✅ **Complete package** (~100MB+ DMG)
- ✅ **Installation scripts** included
- ✅ **Uninstaller** included
- ✅ **Documentation** bundled
- ✅ **Wheel package** included

### macOS Build Comparison

| Feature | `build_dmg.sh` | `build_macos_installer.sh` |
|---------|---------------|---------------------------|
| **Purpose** | 📦 Simple DMG | 📦 Complete installer |
| **Build Time** | Fast (~2-3min) | Thorough (~5-10min) |
| **DMG Size** | ~55MB | ~100MB+ |
| **Contents** | Apps only | Apps + scripts + docs |
| **Installation** | Drag & drop | Automated installer |
| **Dependencies** | None | None (all embedded) |
| **Use Case** | Quick distribution | Professional release |
| **Target** | End users | Enterprise deployment |

### macOS Usage Workflow

#### 🚀 Quick Distribution
```bash
# 1. Build simple DMG
./build_dmg.sh

# 2. Distribute OSTG-TrafficGenerator-0.1.52.dmg
# Users drag apps to Applications folder
```

#### 🏢 Professional Release
```bash
# 1. Build complete installer
./build_macos_installer.sh

# 2. Distribute comprehensive package
# Includes installation scripts and documentation
```

## Deployment Scripts

OSTG provides flexible deployment options for different scenarios:

### Main Deployment Script (`deploy.sh`)

Comprehensive deployment script with full control:

```bash
# Full deployment with all features
./deploy.sh

# Deploy only wheel package (fastest)
./deploy.sh -t wheel-only

# Deploy to custom server
./deploy.sh -H server.com -u admin -p password -t full

# Deploy without backup (faster)
./deploy.sh -t source-only -n -v
```

**Options:**
- `-t, --type TYPE` - Deployment type (full, wheel-only, source-only, config-only)
- `-H, --host HOST` - Target server hostname or IP address
- `-u, --user USER` - SSH username
- `-p, --pass PASS` - SSH password
- `-P, --path PATH` - Remote installation path
- `-n, --no-backup` - Skip creating backup
- `-v, --no-verify` - Skip installation verification
- `-s, --no-start` - Don't start server after deployment
- `-f, --force-rebuild` - Force rebuild even if no changes detected

### Interactive Deployment (`deploy_quick.sh`)

User-friendly interactive wrapper:

```bash
# Interactive deployment menu
./deploy_quick.sh
```

**Menu Options:**
1. Full Deployment (rebuild + deploy everything)
2. Source Code Only (deploy code changes)
3. Wheel Package Only (deploy built package)
4. Configuration Only (deploy config files)
5. Force Rebuild & Deploy (rebuild even if no changes)
6. Deploy Without Backup (faster deployment)
7. Custom deployment with options
8. Deploy to different server

### Deployment Types

#### Full Deployment (`full`)
- Rebuilds the project if needed
- Deploys wheel package
- Updates source files
- Creates backup
- Verifies installation
- Starts server

#### Wheel-Only Deployment (`wheel-only`)
- Deploys only the wheel package
- Faster than full deployment
- No source file updates

#### Source-Only Deployment (`source-only`)
- Reinstalls the wheel package (since source files are included)
- Useful for code changes without rebuilding
- Faster than full deployment

#### Configuration-Only Deployment (`config-only`)
- Deploys only configuration files
- Fastest deployment option
- For config changes only

### Deployment Examples

#### Development Workflow
```bash
# Quick development cycle
./rebuild_quick.sh
./deploy.sh -t wheel-only
```

#### Production Deployment
```bash
# Thorough build and deployment
./rebuild_wheel.sh
./deploy.sh -H production-server.com -u root -p secret -t full
```

#### Different Servers
```bash
# Deploy to multiple servers
./deploy.sh -H server1.com -u admin -p pass -t wheel-only
./deploy.sh -H server2.com -u admin -p pass -t wheel-only
```

#### Environment Variables
```bash
export SERVER_HOST="server.com"
export SERVER_USER="admin"
export SERVER_PASS="password"
export SERVER_PATH="/opt/OSTG"
./deploy.sh -t full
```

## Installation Script

### Complete Installation (`install_ostg_complete.py`)

Comprehensive first-time installation script:

```bash
# Local installation to /opt/OSTG
sudo python3 install_ostg_complete.py

# Remote installation
python3 install_ostg_complete.py -H server.com -u root -p password

# Custom installation directory
python3 install_ostg_complete.py -d /custom/path
```

**Features:**
- Complete OSTG installation with all dependencies
- Python 3.9+ installation and configuration
- PyQt5 GUI framework and dependencies
- Docker Engine installation and configuration
- FRR Docker containers setup
- Systemd services configuration
- Virtual environment creation with build tools
- Comprehensive system dependencies installation
- Network analysis tools (nmap, netcat, socat, etc.)
- System monitoring tools (iotop, nethogs, iftop, htop)
- Development tools (vim, nano, git, jq, yq)
- Archive and compression tools (zip, unzip, tar, gzip)
- Network utilities (traceroute, mtr, bridge-utils, vlan)
- Security tools (SSH client/server)
- Multi-distribution support (Ubuntu/Debian, CentOS/RHEL, Alpine, openSUSE)
- Remote installation support
- Build dependencies for compiled Python packages
- Development tools for testing and code quality

**Options:**
- `-H, --host HOST` - Remote server hostname or IP
- `-u, --user USER` - SSH username for remote installation
- `-p, --pass PASS` - SSH password for remote installation
- `-d, --dir DIR` - Installation directory (default: /opt/OSTG)
- `-w, --wheel-dir DIR` - Wheel source directory (default: dist)
- `-h, --help` - Show help information

**Installation Steps:**
1. **System Dependencies Installation**
   - Python 3.9+ with build tools
   - PyQt5 GUI framework and Qt5 dependencies
   - Network analysis and monitoring tools
   - Development and system utilities
   - Multi-distribution package management

2. **Additional Tools Installation**
   - Network utilities (nmap, netcat, socat, bridge-utils, vlan)
   - System monitoring (iotop, nethogs, iftop, htop)
   - Development tools (vim, nano, git, jq, yq)
   - Archive tools (zip, unzip, tar, gzip)
   - Network diagnostics (traceroute, mtr)
   - Security tools (SSH client/server)

3. **Docker Installation**
   - Docker Engine installation and configuration
   - Docker service startup and verification
   - User permissions configuration

4. **Python Environment Setup**
   - Python 3.9+ installation (if not present)
   - Virtual environment creation
   - Build dependencies installation (cython, numpy, cffi)
   - pip, setuptools, wheel upgrade

5. **OSTG Package Installation**
   - OSTG wheel package installation
   - Additional Python dependencies (psutil, requests, PyYAML, ipaddress)
   - Development tools (pytest, black, flake8, mypy) - optional

6. **FRR Docker Container Setup**
   - FRR container creation and configuration
   - Network bridge setup
   - BGP and routing daemon configuration

7. **Systemd Services Configuration**
   - OSTG server service creation
   - OSTG client service creation
   - Service enablement and startup configuration

8. **Verification and Testing**
   - Installation verification
   - FRR functionality testing
   - Service startup and health checks
   - Network connectivity testing

## Configuration Files

### Deployment Configuration

#### `deploy_config.conf`
Main deployment configuration file:
```ini
# Server Configuration
SERVER_HOST=server.com
SERVER_USER=admin
SERVER_PASS=password
SERVER_PATH=/opt/OSTG
TEMP_PATH=/tmp

# Deployment Options
DEPLOY_TYPE=full
BACKUP_ENABLED=true
VERIFY_INSTALL=true
START_SERVER=true
CLEAN_TEMP=true
```

#### `deploy_config_example.conf`
Example configuration file with all options:
```ini
# Example deployment configuration
# Copy to deploy_config.conf and modify as needed

# Server Configuration
SERVER_HOST=your-server.com
SERVER_USER=root
SERVER_PASS=your-password
SERVER_PATH=/opt/OSTG
TEMP_PATH=/tmp

# Deployment Options
DEPLOY_TYPE=full
BACKUP_ENABLED=true
VERIFY_INSTALL=true
START_SERVER=true
CLEAN_TEMP=true
```

### Environment Variables

All scripts support environment variables for configuration:

```bash
# Server configuration
export SERVER_HOST="server.com"
export SERVER_USER="admin"
export SERVER_PASS="password"
export SERVER_PATH="/opt/OSTG"
export TEMP_PATH="/tmp"

# Installation configuration
export INSTALL_DIR="/opt/OSTG"
export WHEEL_SOURCE_DIR="dist"

# Deployment configuration
export DEPLOY_TYPE="full"
export BACKUP_ENABLED="true"
export VERIFY_INSTALL="true"
export START_SERVER="true"
export CLEAN_TEMP="true"
```

## Best Practices

### Development Workflow
1. **Make changes** to source code
2. **Quick rebuild**: `./rebuild_quick.sh`
3. **Quick deployment**: `./deploy.sh -t wheel-only`
4. **Test changes** on development server

### Production Deployment
1. **Comprehensive rebuild**: `./rebuild_wheel.sh`
2. **Full deployment**: `./deploy.sh -t full`
3. **Verify installation** and test
4. **Monitor logs**: `journalctl -u ostg-server -f`

### Multi-Server Deployment
```bash
# Deploy to multiple servers
for server in server1.com server2.com server3.com; do
    ./deploy.sh -H $server -u admin -p password -t wheel-only
done
```

### Backup Strategy
- Always enable backups for production deployments
- Use `-n` flag only for development/testing
- Backup files are created with timestamps

### Security Considerations
- Use environment variables instead of hardcoded passwords
- Consider using SSH keys instead of passwords
- Limit server access to necessary users only
- Regularly update dependencies

## Script Troubleshooting

### Common Issues and Solutions

#### Build Script Issues

**Issue**: `rebuild_quick.sh` fails with "python3 not found"
```bash
# Solution: Install Python 3
sudo apt-get update
sudo apt-get install python3 python3-pip python3-venv
```

**Issue**: `rebuild_wheel.sh` fails during BGP verification
```bash
# Solution: Check if BGP timer fixes are present
grep -n "timers.*keepalive.*hold_time" utils/frr_docker.py
grep -n "bgp_keepalive.*bgp_hold_time" run_tgen_server.py
```

**Issue**: Wheel build fails with "No module named 'build'"
```bash
# Solution: Install build dependencies
pip3 install --upgrade pip setuptools wheel build
```

#### Deployment Script Issues

**Issue**: SSH connection fails during deployment
```bash
# Solution: Test SSH connection manually
ssh -o ConnectTimeout=10 $SERVER_USER@$SERVER_HOST

# Check if sshpass is installed
sudo apt-get install sshpass
```

**Issue**: Server path doesn't exist
```bash
# Solution: Create directory on remote server
ssh $SERVER_USER@$SERVER_HOST "mkdir -p $SERVER_PATH"
```

**Issue**: Wheel installation fails on remote server
```bash
# Solution: Check Python version and pip on remote server
ssh $SERVER_USER@$SERVER_HOST "python3 --version && pip3 --version"

# Install pip if missing
ssh $SERVER_USER@$SERVER_HOST "sudo apt-get install python3-pip"
```

#### Installation Script Issues

**Issue**: Docker installation fails
```bash
# Solution: Install Docker manually
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

**Issue**: Systemd service fails to start
```bash
# Solution: Check service status and logs
sudo systemctl status ostg-server
sudo journalctl -u ostg-server -f

# Reload systemd and restart
sudo systemctl daemon-reload
sudo systemctl restart ostg-server
```

**Issue**: Virtual environment creation fails
```bash
# Solution: Install python3-venv
sudo apt-get install python3-venv

# Or use alternative method
python3 -m virtualenv ostg_env
```

### Debug Mode

Enable debug mode for detailed output:

```bash
# Enable bash debug mode
set -x

# Run scripts with debug output
./deploy.sh -t full 2>&1 | tee deployment.log

# Disable debug mode
set +x
```

### Log Files

Check these log files for troubleshooting:

```bash
# Deployment logs
tail -f deployment.log

# Server logs
journalctl -u ostg-server -f

# Docker logs
docker logs ostg_frr_container

# Build logs
cat rebuild_info.txt
```

### Verification Commands

Verify installation and deployment:

```bash
# Check if OSTG is installed
python3 -c "import ostg; print('OSTG installed successfully')"

# Check if commands are available
which ostg-server
which ostg-client

# Check server status
systemctl is-active ostg-server

# Check if server is listening
netstat -tlnp | grep :5051
```

### 1. System Dependencies

```bash
# Create virtual environment
python3 -m venv ostg-env
source ostg-env/bin/activate

# Install Python dependencies
pip install flask flask-cors psutil scapy requests pyqt5
```

### 3. Verify Installation

```bash
# Test Python imports
python3 -c "
from flask import Flask
from scapy.all import Ether, Dot1Q, MPLS
import psutil
print('✅ All dependencies installed successfully')
"
```

## Quick Start

### 1. Start the Server

```bash
# Start the server (after installation)
ostg-server

# Or using systemd service
sudo systemctl start ostg-server
```

### 2. Start the Client

```bash
# Start the client GUI
ostg-client

# Or using systemd service
sudo systemctl start ostg-client
```

### 3. Basic Usage

1. **Add Server**: In the client, go to "Server" tab and add your server
2. **Configure Devices**: Go to "Devices" tab and add network devices
3. **Generate Traffic**: Go to "Streams" tab and create traffic streams
4. **Configure Protocols**: Use BGP, OSPF, or IS-IS tabs for protocol configuration

### 4. Docker + FRR Features

OSTG now includes integrated Docker + FRR support:

- **Automatic Container Management**: FRR containers are created automatically when devices are added
- **BGP/OSPF/IS-IS Support**: Full routing protocol support in isolated containers
- **Network Isolation**: Each device gets its own FRR container with isolated networking
- **Easy Management**: Use `ostg-docker-install` command to manage Docker setup

```bash
# Check Docker + FRR status
ostg-docker-install --verify-only

# Rebuild FRR containers
ostg-docker-install --skip-docker
```

## Architecture

```
┌─────────────────┐    HTTP/API    ┌─────────────────┐
│   OSTG Client   │◄──────────────►│   OSTG Server   │
│   (PyQt5 GUI)   │                │   (Flask API)   │
└─────────────────┘                └─────────────────┘
         │                                   │
         │                                   │
    ┌────▼────┐                         ┌────▼────┐
    │ Session │                         │ Network │
    │ Storage │                         │ Config  │
    └─────────┘                         └─────────┘
```

### Components

- **Client**: PyQt5-based GUI for configuration and monitoring
- **Server**: Flask-based API server for network operations
- **Protocols**: FRR integration for BGP, OSPF, IS-IS
- **Traffic**: Scapy-based packet generation
- **Monitoring**: Real-time statistics and status tracking

## Client-Server Communication

### Server Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/frr/status` | GET | Check FRR daemon status |
| `/api/device/add` | POST | Add network device |
| `/api/device/remove` | POST | Remove network device |
| `/api/device/apply` | POST | Apply device configuration |
| `/api/device/arp/check` | POST | Check ARP resolution |
| `/api/device/arp/request` | POST | Send ARP request |
| `/api/device/ping` | POST | Ping device |
| `/api/device/bgp/configure` | POST | Configure BGP |
| `/api/traffic/start` | POST | Start traffic stream |
| `/api/traffic/stop` | POST | Stop traffic stream |
| `/api/streams/stats` | GET | Get stream statistics |

## Device Management

### Adding a Device

```bash
curl -X POST http://localhost:5050/api/device/add \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "device_name": "Router-1",
    "interface": "enp180s0np0",
    "mac_address": "00:11:22:33:44:55",
    "ipv4": "192.168.1.10",
    "ipv4_mask": "24",
    "ipv6": "2001:db8::1",
    "ipv6_mask": "64",
    "vlan": "100",
    "gateway": "192.168.1.1"
  }'
```

### Device Status Check

```bash
curl -X POST http://localhost:5050/api/device/arp/check \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "interface": "enp180s0np0",
    "ipv4": "192.168.1.10",
    "gateway": "192.168.1.1"
  }'
```

### Ping Device

```bash
curl -X POST http://localhost:5050/api/device/ping \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "interface": "enp180s0np0",
    "ipv4": "192.168.1.10",
    "target": "192.168.1.1"
  }'
```

## Topology Tab

A new top-level tab modelled after IXIA IXNetwork's topology editor. It's a read-only fabric visualisation — to create or edit devices, use the Devices tab; the canvas re-renders from the device DB on Refresh.

### Visual elements

| Element | Meaning |
| --- | --- |
| **Port badge** (green pill at the bottom of each column) | One per unique server-side interface; labelled with the interface name and bound-device count. |
| **Device card** (white rectangle with header + chip stack) | One per device. The header has a status LED, the device name, and an optional `[xN]` multiplicity badge. |
| **Status LED** | Green = all configured protocols up, amber = some up, red = configured but none up, grey = nothing configured. |
| **Protocol chip** | One per protocol the device has configured — ETH, IPv4, IPv6, BGP, OSPF, ISIS, DHCP. Fill turns green when that protocol is up. |
| **Cable** (thick green curve) | Card → its port badge. |
| **Peer edge** (thin line between cards) | BGP (solid grey), OSPF (dashed purple), IS-IS (dashed cyan). Multiple protocols between the same pair stack with a perpendicular offset. |

### Controls

- **Refresh** — pulls `/api/device/database/devices` on a `QThread` worker so the GUI stays responsive on slow servers.
- **Reset layout** — clears saved positions for the current layout mode and re-runs the algorithm.
- **Layout combo** — Hierarchical (default; ports at bottom, devices stacked above) or Circular. Position cache is per-mode so toggling doesn't clobber the other layout.
- **Zoom**: scroll wheel (cursor-anchored), or the toolbar `+` / `−` / `Fit` buttons. Clamped to `[0.2×, 6×]`.
- **Pan** — drag empty canvas.
- **Drag a card** — repositions it. The cable and any peer edges re-route in real time.
- **Click a card** — populates the **right-side property panel** with device metadata, per-protocol detail (peer addresses, AS, lease IP, etc.), and a "Recent transitions" block fetched asynchronously from the state-history endpoint.
- **Double-click a card** — opens the full server-side JSON config in a read-only viewer with copy-to-clipboard.

## Stateful TCP

A real-socket TCP traffic generator that runs **in parallel** to the scapy-based stateless streams. Sessions here complete actual 3-way handshakes, so middleboxes, NAT, proxies, and load balancers see real connection state.

### When to use which

| Use case | Use… |
| --- | --- |
| Line-rate L2/L3 forwarding, latency under load | Scapy/DPDK streams (existing). |
| "Does my middlebox proxy TCP correctly?" | Stateful TCP. |
| HTTP-aware tests (WAF, reverse proxy, gateway) | Stateful TCP, `protocol=http`. |
| TLS handshake testing | Stateful TCP, `tls=true`. |
| Per-VRF source pinning | Stateful TCP, `vrf=<iface>` (Linux). |
| Retransmit / kernel-RTT visibility | Stateful TCP, `TCP_INFO` (Linux). |

### Quick example

```bash
# Echo server (loopback)
netgen-cli tcp start-server --port 5001 --bind 127.0.0.1
# → { "session_id": "9286ba6e-..." }

# Fire a client at it for 10s, 4 parallel senders
netgen-cli tcp start-client \
  --dst-ip 127.0.0.1 --dst-port 5001 \
  --duration 10 --concurrency 4 \
  --payload-bytes 4096
# → { "session_id": "47dce96a-..." }

# Live stats
netgen-cli tcp stats --session-id 47dce96a-...
# → { "counters": { "conns_established": 8120, "bytes_tx": 33259520, ... } }

# Stop everything
netgen-cli tcp stop
```

### HTTP / TLS

```bash
# HTTP 1.1 echo (POST → 200 OK, Content-Length-framed)
netgen-cli tcp start-server --port 8080 --protocol http --response-bytes 4096
netgen-cli tcp start-client --dst-ip 127.0.0.1 --dst-port 8080 \
    --protocol http --duration 10 --concurrency 8
# stats: http_status_2xx counter increments per round-trip

# TLS — self-signed cert (test environments)
netgen-cli tcp start-server --port 8443 \
    --tls --tls-cert cert.pem --tls-key key.pem
netgen-cli tcp start-client --dst-ip 127.0.0.1 --dst-port 8443 \
    --tls --duration 10
# (tls_verify defaults to off — set --tls-verify to enforce CA + hostname)
```

### VRF binding (Linux)

```bash
# Pin client source onto a per-device VRF
netgen-cli tcp start-client \
    --dst-ip 10.0.0.5 --dst-port 5001 \
    --vrf vrf-abc12345 --src-ip 10.0.0.10
```

On macOS / Windows the `--vrf` flag is a no-op and surfaces a warning on `last_error`; traffic still flows via the main routing table.

### What's surfaced in counters

Per session the registry returns:
- `conns_attempted / conns_established / conns_failed`
- `bytes_tx / bytes_rx`
- `avg_handshake_ms` — userspace `connect()` time
- `avg_rtt_ms` — userspace round-trip including send + recv
- `avg_kernel_rtt_us`, `kernel_rtt_samples` — from `TCP_INFO.rtt_us` (Linux)
- `retransmits_total` — from `TCP_INFO.total_retrans` (Linux)
- `http_status_2xx / http_status_other` — only when `protocol=http`
- `last_error` — string snapshot of the most recent error (TLS handshake fail, bind error, etc.)

## netgen-cli (Headless CLI)

`netgen-cli` is the headless companion to the GUI — every common multi-device workflow is one command. Useful in CI, tmux panes, and SSH sessions without an X display.

```bash
netgen-cli health                                      # /api/health + monitor health
netgen-cli list                                        # device DB rows
netgen-cli export -o devices.json                      # snapshot topology
netgen-cli import -f devices.json --wait               # restore + wait for ARP
netgen-cli apply -f one_device.json --wait             # apply a single device
netgen-cli status -i 47dce96a-...                      # device + protocol status
netgen-cli wait -i 47dce96a-... --timeout 60           # block until ARP resolves

netgen-cli tcp start-client --dst-ip ... --dst-port ...
netgen-cli tcp start-server --port ...
netgen-cli tcp stop [--session-id ...]
netgen-cli tcp list
netgen-cli tcp stats --session-id ...
```

**Server URL**: defaults to `$NETGEN_SERVER_URL`, else `http://localhost:5050`. Override with `-s URL`.

**Auth**: if `$NETGEN_AUTH_TOKEN` is set, every request gets `Authorization: Bearer …` automatically (matches the GUI's behaviour).

## Traffic Generation API

### Start IPv4 Stream

```bash
curl -X POST http://localhost:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp180s0np0": [
        {
          "name": "IPv4_Stream",
          "enabled": true,
          "frame_size": 512,
          "mac_source_address": "00:11:22:33:44:55",
          "mac_destination_address": "66:77:88:99:aa:bb",
          "ipv4_source": "192.168.1.10",
          "ipv4_destination": "192.168.1.20",
          "udp_source_port": 12345,
          "udp_destination_port": 54321,
          "vlan_id": "100",
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 1000,
          "stream_duration_mode": "Continuous",
          "flow_tracking_enabled": true,
          "stream_id": "stream-001"
        }
      ]
    }
  }'
```

### Start IPv6 Stream

```bash
curl -X POST http://localhost:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp180s0np0": [
        {
          "name": "IPv6_Stream",
          "enabled": true,
          "frame_size": 512,
          "mac_source_address": "00:aa:bb:cc:dd:ee",
          "mac_destination_address": "11:22:33:44:55:66",
          "L3": "IPv6",
          "L4": "UDP",
          "ipv6_source": "2001:db8::1",
          "ipv6_destination": "2001:db8::2",
          "udp_source_port": 1220,
          "udp_destination_port": 5678,
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 500,
          "stream_duration_mode": "Continuous",
          "stream_id": "stream-002"
        }
      ]
    }
  }'
```

### Start RoCEv2 Stream

```bash
curl -X POST http://localhost:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp180s0np0": [
        {
          "name": "RoCEv2_Stream",
          "enabled": true,
          "frame_size": 256,
          "mac_source_address": "00:aa:bb:cc:dd:ee",
          "mac_destination_address": "00:11:22:33:44:55",
          "ipv6_source": "::1",
          "ipv6_destination": "::2",
          "L4": "RoCEv2",
          "rocev2": {
            "rocev2_source_gid": "0:0:0:0:0:ffff:10.1.1.1",
            "rocev2_destination_gid": "0:0:0:0:0:ffff:10.1.1.2",
            "rocev2_source_qp": "100",
            "rocev2_destination_qp": "200",
            "rocev2_opcode": "SendOnly",
            "rocev2_flow_label": "55555",
            "rocev2_traffic_class": "2"
          },
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 100,
          "stream_duration_mode": "Continuous",
          "stream_id": "stream-003"
        }
      ]
    }
  }'
```

### Stop Stream

```bash
curl -X POST http://localhost:5050/api/traffic/stop \
  -H "Content-Type: application/json" \
  -d '{
    "streams": [
      {
        "interface": "enp180s0np0",
        "stream_id": "stream-001"
      }
    ]
  }'
```

### Get Stream Statistics

```bash
curl -G http://localhost:5050/api/streams/stats \
  --data-urlencode "interface=enp180s0np0"
```

The response includes per-stream `dpdk_enable` (bool) and
`dpdk_tx_cores` (int) so clients can render the engine and queue count
inline.

## DPDK Multi-Queue Scaling

For 100G/400G line-rate generation, a single TX queue is the bottleneck.
The `tx_worker` binary supports **N TX queues driven by N pinned worker
lcores inside one primary process**, controlled by the per-stream
`dpdk_tx_cores` field.

### Enable from the desktop client

In the **Add/Edit Stream** dialog, on the **Variable Fields** tab:

- Tick **Use DPDK (tx_worker)**
- Set **TX Cores (queues)** to 1 / 2 / 4 / 8 / 12 / 16
- Click **Recommend** to ask the server for a calibrated suggestion
  based on the interface's link speed, the chosen frame size, and the
  target rate.

The running stream is visible in the Statistics dock's
**Stream Statistics** tab — the **Engine** column shows
`DPDK ×N` while the stream is active.

### Enable from the API

```bash
curl -X POST http://localhost:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp181s0f0np0": [{
        "name": "BlastUDP",
        "enabled": true,
        "frame_size": 1500,
        "stream_rate_type": "Line Rate",
        "L4": "UDP",
        "dpdk_enable": true,
        "dpdk_tx_cores": 4
      }]
    }
  }'
```

`dpdk_tx_cores` is also accepted under `protocol_selection` and via the
`DPDK_TX_CORES` env var on the server (defaults to 1 = backwards
compatible single-queue path).

### Recommendation endpoint

```bash
curl -G http://localhost:5050/api/dpdk/recommend \
  --data-urlencode "iface=enp181s0f0np0" \
  --data-urlencode "frame_size=1500" \
  --data-urlencode "pps=0"
```

Returns:

```json
{
  "ok": true,
  "iface": "enp181s0f0np0",
  "link_speed_mbps": 400000,
  "frame_size": 1500,
  "target_pps": 32894736,
  "line_rate_pps": 32894736,
  "estimated_pps_per_core": 4100000,
  "recommended_tx_cores": 12,
  "explanation": "Link 400 Gbps; line rate at 1500B = 32,894,736 pps. ..."
}
```

### Calibrated numbers (Mellanox CX-7, AMD EPYC, NUMA-pinned cores)

Measured on `enp181s0f0np0` (BDF `0000:b5:00.0`, NUMA 1):

| Cores | 64B Mpps | 64B % of 100G | 1500B Gbps |
| ----: | -------: | ------------: | ---------: |
|     1 |     4.54 |            3% |         49 |
|     2 |     9.14 |            6% | **99.6** *(100G saturated)* |
|     4 |    18.21 |           12% |        199 |
|     8 |    35.66 |           24% | **385** *(approaches 400G)* |
|    12 |    48.12 |           32% |          – |
|    16 |    58.24 |           39% |          – |

Linear scaling holds to 8 cores; 80–90% efficiency at 12–16. Past 16
the bottleneck is per-queue PMD throughput and PCIe overhead, not
software. Per-core ceiling estimates used by the recommender:

- 64B: 4.5 Mpps/core
- 512B: 4.3 Mpps/core
- 1500B: 4.1 Mpps/core

### Installing DPDK on the Linux server

Two paths. Pick by whether you're installing the whole Netgen server
(DPDK is one step of many) or just want DPDK alone.

**Path 1 — full Netgen server install (DPDK included).** This is
the normal case; runs `install_dpdk.sh` automatically as part of
the bigger install.

```bash
git clone https://github.com/amishagrawal2001-arch/netgen.git
cd netgen
./rebuild_quick.sh                                    # build the wheel
sudo python3 install_ostg_complete.py                 # full server: wheel + Docker + FRR + DPDK + systemd

# Remote install from your laptop:
python3 install_ostg_complete.py -H lab-box -u root -p '<password>'

# Skip DPDK entirely (devbox / no NIC to bind):
sudo python3 install_ostg_complete.py --no-dpdk

# Apt-deps only, skip the 10–30 min DPDK build (you'll meson it later):
sudo python3 install_ostg_complete.py --skip-dpdk-build
```

Watch the DPDK build progress in another terminal:
```bash
ssh root@lab-box 'tail -f /var/log/netgen-install-dpdk.log'
```

**Path 2 — DPDK only (run the installer script directly).** Use
when Netgen is already installed and you just want to (re)do DPDK:

```bash
sudo /opt/OSTG/resources/dpdk/install_dpdk.sh

# Non-interactive (defaults: clones DPDK to ~/SURAJ/dpdk, 1024 hugepages):
sudo AUTO_MODE=1 /opt/OSTG/resources/dpdk/install_dpdk.sh

# Apt-deps + tx_worker only, skip the meson DPDK build:
sudo SKIP_BUILD=1 /opt/OSTG/resources/dpdk/install_dpdk.sh

# Use an existing DPDK source tree:
sudo DPDK_DIR=/opt/dpdk /opt/OSTG/resources/dpdk/install_dpdk.sh
```

**What the installer does, in order**:

1. **Preflight** — must be root, Linux only.
2. **Detect DPDK source** at `~/SURAJ/dpdk`, `~/dpdk`, `/opt/dpdk`,
   or `/usr/src/dpdk`. If missing, offers to
   `git clone https://dpdk.org/git/dpdk` (v23.11) — **needs internet**.
3. **apt install** — toolchain
   (`build-essential meson ninja-build pkg-config`), runtime libs
   (`libnuma-dev libelf-dev libpcap-dev`), Mellanox PMD prereqs
   (`libibverbs-dev libmlx5-dev rdma-core`), and
   `linux-headers-$(uname -r)` for vfio-pci on cloud kernels.
4. **Build DPDK** with
   `meson setup build -Dexamples=all -Ddisable_drivers=net/mana`
   then `ninja -C build` then `ninja -C build install` (10–30 min).
5. **Build tx_worker** (Netgen's DPDK packet-injector) against the
   just-installed libdpdk.
6. **Hugepages** — write `vm.nr_hugepages` to
   `/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages` AND drop
   `/etc/sysctl.d/99-netgen-hugepages.conf` so it survives reboots.
7. **NIC binding** — `dpdk-devbind.py --bind=vfio-pci <BDF>` for
   Intel; for Mellanox / mlx5 it deliberately stays bound to
   `mlx5_core` (the mlx5 PMD wants the kernel driver loaded).
8. **ld.so config** — adds `/usr/local/lib/x86_64-linux-gnu` to
   `/etc/ld.so.conf.d/dpdk.conf` and runs `ldconfig`.
9. **Verify** — runs a smoke test.

**Manual prerequisite (before running the script).** For Intel NICs
you also need IOMMU enabled in the bootloader — the script warns
but won't touch GRUB:
```bash
# /etc/default/grub
GRUB_CMDLINE_LINUX="... intel_iommu=on iommu=pt"
sudo update-grub && sudo reboot
```

For Mellanox NICs **don't** rebind to vfio-pci — the script handles
that automatically; just make sure `mlx5_core` is loaded
(`lsmod | grep mlx5`).

**Verify after install**:
```bash
grep Huge /proc/meminfo                     # hugepages allocated?
sudo dpdk-devbind.py --status               # DPDK bind state?
sudo /opt/OSTG/resources/dpdk/verify_dpdk.sh
sudo systemctl start netgen-server
sudo systemctl status netgen-server
```

**Troubleshooting**:

| Symptom | Likely cause | Fix |
|---|---|---|
| `meson setup` fails: "couldn't find libibverbs" | Mellanox prereqs missed | `sudo apt install libibverbs-dev libmlx5-dev rdma-core` |
| `vfio-pci: bind failed` | IOMMU off | Add `intel_iommu=on iommu=pt` to GRUB, reboot |
| `EAL: No free hugepages reported` | Hugepages didn't persist (pre-fix) | New install drops `99-netgen-hugepages.conf`; re-run install OR `echo 1024 \| sudo tee /proc/sys/vm/nr_hugepages` |
| `link: undefined reference to ibv_cmd_query_*` (mana driver) | Ubuntu 22.04 libibverbs lacks `IBVERBS_PRIVATE_34` | Already disabled by `-Ddisable_drivers=net/mana` |
| tx_worker built but won't start | DPDK ABI mismatch | Re-run `sudo /opt/OSTG/resources/dpdk/install_dpdk.sh` to rebuild against current libdpdk |

Full install_dpdk.sh log lands at `/var/log/netgen-install-dpdk.log`
when run via `install_ostg_complete.py`.

### Prerequisites

- DPDK 21.11+ (uses `RTE_LCORE_FOREACH_WORKER`,
  `RTE_ETH_TX_OFFLOAD_*`)
- Hugepages allocated on the NIC's NUMA node (1G recommended)
- For Intel/AMD: `intel_iommu=on iommu=pt` / `amd_iommu=on iommu=pt`
- Mellanox: kernel `mlx5_core` driver alongside DPDK (no vfio bind
  needed). Broadcom: vfio-pci bind via the `/admin` portal.
- EAL `-l` corelist must contain `1 + dpdk_tx_cores` cores; the
  launcher allocates them from the NIC's NUMA node automatically.

### How it works under the hood

`utils/dpdk_tx_worker.py` resolves `dpdk_tx_cores` from the stream
JSON, allocates `1 + N` lcores on the NIC's NUMA node via
`_pick_corelist_on_node(numa, count=1+N)`, and passes
`--tx-cores N` to the binary.

`tx_worker.c` configures `rte_eth_dev_configure(port, 1, N, ...)`,
sets up N TX queues, and launches a `tx_loop(struct tx_worker_ctx*)`
per worker via `rte_eal_remote_launch`. Each worker drains its own
queue, owns its own `seq` counter, and publishes per-burst
`volatile sent/dropped` counts that the main thread aggregates
into `STAT` lines once per second.

The PPS target is split evenly across workers (remainder spread to
the first few). `pps=0` (line rate) means each worker floods its
queue uncapped.

## Protocol Configuration

### BGP Configuration

```bash
curl -X POST http://localhost:5050/api/device/bgp/configure \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "device_name": "Router-1",
    "interface": "enp180s0np0",
    "vlan": "100",
    "ipv4": "192.168.1.10",
    "bgp": {
      "bgp_mode": "eBGP",
      "bgp_asn": "65000",
      "bgp_remote_asn": "65001",
      "bgp_neighbor_ipv4": "192.168.1.20",
      "bgp_update_source_ipv4": "192.168.1.10"
    }
  }'
```

### Check FRR Status

```bash
curl -G http://localhost:5050/api/frr/status
```

## Monitoring and Troubleshooting

Netgen exposes two server-side monitoring surfaces — a built-in web
admin portal and the systemd/log layer. Both come pre-installed with
`install_ostg_complete.py` / `install_turnkey.sh`.

### Server Admin Portal (`/admin`)

Single-page UI at `http://<server>:5050/admin`. Auth-exempt (so it
works before you set `NETGEN_AUTH_TOKEN`). Renders inline — no
separate template files, no static assets to deploy.

What it shows / lets you do:

| Panel | Shows | Action |
|---|---|---|
| Server health | hostname, port, libdpdk version, IOMMU enabled?, vfio module state, hugepages allocated, tx_worker built? | refresh |
| DPDK install | live tail of `install_dpdk.sh` log | **"Install DPDK"** button → runs `install_dpdk.sh --auto` in the background |
| Interfaces | All NICs with PCI BDF, kernel driver, NUMA node, IP, bind state. Recovers original name after vfio binding via `/tmp/netgen_admin_bind_history.json` | bind to vfio-pci / rebind to kernel per row |
| Hugepages | current `nr_hugepages` per NUMA node | reconfigure |
| IOMMU | reads `/proc/cmdline` for `intel_iommu=on` / `amd_iommu=on` | warns if missing |

All underlying actions are also callable via curl:

```bash
# Consolidated health (scrape-friendly)
curl http://lab-box:5050/api/admin/health | jq

# DPDK control
curl http://lab-box:5050/api/dpdk/status
curl http://lab-box:5050/api/dpdk/interfaces
curl -X POST http://lab-box:5050/api/dpdk/bind   -d '{"pci":"0000:01:00.0"}'
curl -X POST http://lab-box:5050/api/dpdk/unbind -d '{"pci":"0000:01:00.0"}'
curl http://lab-box:5050/api/dpdk/verify
curl -X POST http://lab-box:5050/api/dpdk/hugepages   -d '{"pages":1024}'
curl -X POST http://lab-box:5050/api/dpdk/iommu                  # checks /proc/cmdline
curl -X POST http://lab-box:5050/api/dpdk/load_modules           # modprobe vfio-pci

# Trigger install + tail log
curl -X POST http://lab-box:5050/api/admin/install_dpdk
curl http://lab-box:5050/api/admin/install_dpdk/log
```

### Consolidated health JSON

`GET /api/admin/health` returns one structured payload covering
everything the dashboard panels show — designed for scraping into
Prometheus, Nagios, or a `watch -n 5 curl ...` loop:

```json
{
  "hostname": "lab-box",
  "netgen_server": {"port": 5050},
  "dpdk":      {"installed": true,  "version": "23.11.0"},
  "iommu":     {"enabled": true,    "cmdline_excerpt": "...intel_iommu=on iommu=pt..."},
  "vfio":      {"vfio_pci_loaded": true, "vfio_loaded": true},
  "hugepages": {"total": 1024, "free": 1024, "size_kb": 2048},
  "tx_worker": {"present": true, "path": "/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker"},
  "install_running": false
}
```

### systemd + journalctl

The server runs as a systemd unit installed by
`install_ostg_complete.py`. Use the normal Linux tools:

```bash
# Service state
sudo systemctl status netgen-server

# Live log tail
sudo journalctl -u netgen-server -f

# Last 200 lines
sudo journalctl -u netgen-server -n 200 --no-pager

# Restart / stop / start
sudo systemctl restart netgen-server
```

Log files under `/var/log/`:

| File | Source |
|---|---|
| `/var/log/netgen-install-dpdk.log` | DPDK install (written by `install_ostg_complete.py`) |
| `journalctl -u netgen-server`      | server stdout/stderr |

### Health probe + SSE event stream

For external uptime checks (HAProxy / k8s / Nagios) — auth-exempt,
tiny payload:

```bash
curl -fsS http://lab-box:5050/api/health
# → 200 {"status":"ok",...}  on success, non-2xx on failure
```

For dashboards / client GUIs that want real-time protocol-state
transitions:

```bash
# Server-Sent Events — emits one line per state change, never closes
curl -N http://lab-box:5050/api/events/stream

# Snapshot of the in-memory ring buffer
curl    http://lab-box:5050/api/events/status
```

### Network Interface Monitoring

```bash
# Monitor VLAN traffic
tcpdump -i enp180s0np0 -n -e vlan

# Monitor specific traffic patterns
tcpdump -i enp180s0np0 -n -e vlan -s 0 -XX

# Monitor ICMP traffic
tcpdump -i enp180s0np0 -n icmp

# Monitor TCP traffic on specific ports
tcpdump -i enp180s0np0 tcp src port 22 and dst port 33
```

### Server Logs

```bash
# View server logs in real-time
tail -f /tmp/server.log

# Check server status
ps aux | grep run_tgen_server

# Check FRR daemon status
systemctl status frr
```

### Common Issues

1. **Port Already in Use**: Kill existing server processes
   ```bash
   pkill -f 'run_tgen_server'
   ```

2. **FRR Not Running**: Start FRR service
   ```bash
   sudo systemctl start frr
   ```

3. **Permission Denied**: Ensure running with appropriate privileges
   ```bash
   sudo python run_tgen_server.py --host 0.0.0.0 --port 5050
   ```

## Examples

### Complete Workflow Example

1. **Start Server**:
   ```bash
   python run_tgen_server.py --host 0.0.0.0 --port 5050
   ```

2. **Add Device**:
   ```bash
   curl -X POST http://localhost:5050/api/device/add \
     -H "Content-Type: application/json" \
     -d '{
       "device_id": "router-1",
       "device_name": "Core Router",
       "interface": "enp180s0np0",
       "mac_address": "00:11:22:33:44:55",
       "ipv4": "10.0.1.1",
       "ipv4_mask": "24",
       "vlan": "100",
       "gateway": "10.0.1.254"
     }'
   ```

3. **Configure BGP**:
   ```bash
   curl -X POST http://localhost:5050/api/device/bgp/configure \
     -H "Content-Type: application/json" \
     -d '{
       "device_id": "router-1",
       "device_name": "Core Router",
       "interface": "enp180s0np0",
       "vlan": "100",
       "ipv4": "10.0.1.1",
       "bgp": {
         "bgp_mode": "eBGP",
         "bgp_asn": "65000",
         "bgp_remote_asn": "65001",
         "bgp_neighbor_ipv4": "10.0.1.2",
         "bgp_update_source_ipv4": "10.0.1.1"
       }
     }'
   ```

4. **Start Traffic Stream**:
   ```bash
   curl -X POST http://localhost:5050/api/traffic/start \
     -H "Content-Type: application/json" \
     -d '{
       "streams": {
         "Port:enp180s0np0": [
           {
             "name": "Test_Stream",
             "enabled": true,
             "frame_size": 512,
             "mac_source_address": "00:11:22:33:44:55",
             "mac_destination_address": "66:77:88:99:aa:bb",
             "ipv4_source": "10.0.1.1",
             "ipv4_destination": "10.0.1.2",
             "udp_source_port": 12345,
             "udp_destination_port": 54321,
             "vlan_id": "100",
             "stream_rate_type": "Packets Per Second (PPS)",
             "stream_pps_rate": 1000,
             "stream_duration_mode": "Continuous",
             "flow_tracking_enabled": true,
             "stream_id": "test-stream-001"
           }
         ]
       }
     }'
   ```

5. **Monitor Statistics**:
   ```bash
   curl -G http://localhost:5050/api/streams/stats \
     --data-urlencode "interface=enp180s0np0"
   ```

6. **Stop Stream**:
   ```bash
   curl -X POST http://localhost:5050/api/traffic/stop \
     -H "Content-Type: application/json" \
     -d '{
       "streams": [
         {
           "interface": "enp180s0np0",
           "stream_id": "test-stream-001"
         }
       ]
     }'
   ```

### Multiple Streams Example

```bash
curl -X POST http://localhost:5050/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "Port:enp180s0np0": [
        {
          "name": "ICMP_Stream",
          "enabled": true,
          "frame_size": 64,
          "L3": "IPv4",
          "L4": "ICMP",
          "mac_source_address": "00:00:00:00:00:02",
          "mac_destination_address": "00:00:00:00:00:01",
          "ipv4_source": "192.168.1.10",
          "ipv4_destination": "192.168.1.20",
          "vlan_id": 100,
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 1,
          "stream_duration_mode": "Continuous",
          "stream_id": "icmp-001"
        },
        {
          "name": "UDP_Stream",
          "enabled": true,
          "frame_size": 64,
          "L3": "IPv4",
          "L4": "UDP",
          "mac_source_address": "00:00:00:00:00:02",
          "mac_destination_address": "00:00:00:00:00:01",
          "ipv4_source": "192.168.1.10",
          "ipv4_destination": "192.168.1.20",
          "udp_source_port": 12345,
          "udp_destination_port": 54321,
          "vlan_id": 100,
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 1,
          "stream_duration_mode": "Continuous",
          "stream_id": "udp-001"
        }
      ]
    }
  }'
```

## Support

For issues and questions:
- Check server logs: `tail -f /tmp/server.log`
- Verify FRR status: `systemctl status frr`
- Test network connectivity: `ping <target_ip>`
- Monitor traffic: `tcpdump -i <interface> -n`



## License

This project is open source. Please refer to the license file for details.