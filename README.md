# OSTG (Open Source Traffic Generator)

A comprehensive network traffic generation and device management system with support for various protocols including BGP, OSPF, IS-IS, and advanced traffic patterns.

## Table of Contents

- [Installation](#installation)
- [Build Scripts](#build-scripts)
- [Deployment Scripts](#deployment-scripts)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Client-Server Communication](#client-server-communication)
- [Device Management](#device-management)
- [Traffic Generation API](#traffic-generation-api)
- [Protocol Configuration](#protocol-configuration)
- [Monitoring and Troubleshooting](#monitoring-and-troubleshooting)
- [Examples](#examples)

## Installation

### Quick Installation (Recommended)

For a complete installation with Docker + FRR support:

```bash
# Clone the repository
git clone <repository-url>
cd OSTG

# Build the wheel package
./rebuild_quick.sh

# Install locally to /opt/OSTG
sudo python3 install_ostg_complete.py

# Or install remotely to a server
python3 install_ostg_complete.py -H server.com -u root -p password
```

### Development Environment Setup

For development work with the GUI client:

```bash
# Use the automated installation script
./install_ostg_client_env.sh

# Or manual setup
python3 -m venv ostg_client_env
source ostg_client_env/bin/activate
pip install --find-links https://download.qt.io/snapshots/ci/pyqt5/5.15/wheels/ PyQt5
pip install -r requirements.txt
pip install -e .
```

**Note**: The GUI client automatically handles Qt platform plugin issues on macOS. If you encounter "Could not find the Qt platform plugin 'cocoa'" errors, ensure PyQt5 is installed from Qt's official wheels (not from source).

## Script Overview

OSTG provides a comprehensive set of scripts for building, deploying, and installing the traffic generator:

### 🏗️ Build Scripts
- **`rebuild_quick.sh`** - Fast wheel package build for development
- **`rebuild_wheel.sh`** - Comprehensive wheel package build for production
- **`build_dmg.sh`** - Simple macOS DMG installer (apps only)
- **`build_macos_installer.sh`** - Complete macOS DMG installer (full package)

### 🚀 Deployment Scripts
- **`deploy.sh`** - Flexible deployment to remote servers
- **`deploy_quick.sh`** - Interactive deployment wrapper

### 🏗️ Installation Scripts
- **`install_ostg_complete.py`** - Complete system installation with all dependencies

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