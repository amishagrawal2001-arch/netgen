# OSTG Installation Guide

This guide covers the complete installation of OSTG Traffic Generator with Docker + FRR support.

## Prerequisites

### Supported Operating Systems
- **Ubuntu/Debian** (18.04+, 20.04+, 22.04+)
- **CentOS/RHEL** (7+, 8+, 9+)
- **Alpine Linux** (3.15+)
- **openSUSE** (Leap 15+, Tumbleweed)
- **Fedora** (35+)

### System Requirements
- **CPU:** 2 cores, 2.0 GHz minimum
- **RAM:** 4 GB (8 GB recommended for production)
- **Storage:** 10 GB free space
- **Network:** Ethernet interface(s) for traffic generation
- **Root access** (sudo) for Docker and network configuration
- **Internet connection** for downloading dependencies

### What Gets Automatically Installed
- **Python 3.9+** (installed if not present)
- **Docker Engine** with networking support
- **PyQt5 GUI Framework** for client interface
- **Network Analysis Tools** (nmap, netcat, socat, bridge-utils, vlan)
- **System Monitoring Tools** (iotop, nethogs, iftop, htop)
- **Development Tools** (vim, nano, git, jq, yq)
- **Build Dependencies** (gcc, g++, make, pkg-config)
- **Python Build Tools** (cython, numpy, cffi)
- **Network Utilities** (traceroute, mtr)
- **Security Tools** (SSH client/server)
- **Archive Tools** (zip, unzip, tar, gzip)

## Quick Installation (Recommended)

### Local Installation

For a complete installation with full Docker + FRR features:

```bash
# 1. Build the wheel package
./rebuild_quick.sh

# 2. Run the unified installation script
sudo python3 install_ostg_complete.py
```

### Remote Installation

Install to a remote server:

```bash
# Install to remote server
python3 install_ostg_complete.py -H server.com -u root -p password

# Install to specific IP with custom directory
python3 install_ostg_complete.py -H 192.168.1.100 -u admin -p secret -d /opt/OSTG
```

### Installation Options

```bash
# Custom installation directory
sudo python3 install_ostg_complete.py -d /custom/path

# Use custom wheel source directory
python3 install_ostg_complete.py -w /path/to/wheels

# Show help
python3 install_ostg_complete.py -h
```

This single script will automatically:
- **Install all system dependencies** (Ubuntu/Debian/CentOS/RHEL/Alpine/openSUSE/Fedora)
- **Install Python 3.9+** with build tools (if not present)
- **Install PyQt5 GUI framework** and all Qt5 dependencies
- **Install network analysis tools** (nmap, netcat, socat, bridge-utils, vlan)
- **Install system monitoring tools** (iotop, nethogs, iftop, htop)
- **Install development tools** (vim, nano, git, jq, yq)
- **Install Docker Engine** (if not present)
- **Create Python virtual environment** with build dependencies
- **Install OSTG package** with all Python dependencies
- **Install build dependencies** (cython, numpy, cffi)
- **Build enhanced FRR Docker image** with full features
- **Setup Docker network** for FRR containers
- **Create and enable systemd services**
- **Verify installation** and test FRR functionality

**Enhanced FRR Features Included:**
- BGP, OSPF, OSPFv6, RIP, RIPng, IS-IS
- PIM, PIM6, LDP, NHRP, EIGRP, Babel
- BFD, Fabric, VRRP, PCEP, PBR, Static
- SNMP, FPM, Enhanced logging and monitoring

### Unified Installation Script Features

The `install_ostg_complete.py` script provides:

✅ **Multi-Distribution Support**: Ubuntu/Debian, CentOS/RHEL, Alpine, openSUSE, Fedora
✅ **Automatic OS Detection**: Detects and configures for your specific Linux distribution
✅ **Complete Dependency Management**: Installs all required system packages
✅ **Python 3.9+ Installation**: Installs Python with build tools if not present
✅ **PyQt5 GUI Framework**: Complete GUI dependencies for client interface
✅ **Network Analysis Tools**: nmap, netcat, socat, bridge-utils, vlan
✅ **System Monitoring Tools**: iotop, nethogs, iftop, htop, sysstat
✅ **Development Tools**: vim, nano, git, jq, yq, tree
✅ **Build Dependencies**: gcc, g++, make, pkg-config, libffi-dev, openssl-dev
✅ **Python Build Tools**: cython, numpy, cffi for compiled packages
✅ **Docker Integration**: Automatically installs and configures Docker
✅ **Enhanced FRR**: Builds FRR image with all available routing protocols
✅ **Systemd Services**: Creates and enables automatic startup services
✅ **Remote Installation**: Support for installing to remote servers
✅ **Installation Verification**: Tests all components and FRR functionality
✅ **Error Handling**: Comprehensive error checking and logging
✅ **Cleanup**: Automatic cleanup of test containers and processes

### Post-Installation

After successful installation:

```bash
# Start OSTG services
sudo systemctl start ostg-server
sudo systemctl start ostg-client

# Check service status
sudo systemctl status ostg-server

# View logs
sudo journalctl -u ostg-server -f

# Manual start (for testing)
cd /path/to/installation
source ostg_env/bin/activate
ostg-server --host 0.0.0.0 --port 5051
```

## Installation Process Details

The enhanced installation script performs the following steps:

### Step 1: System Dependencies Installation
- **OS Detection**: Automatically detects Ubuntu/Debian, CentOS/RHEL, Alpine, openSUSE, or Fedora
- **Package Installation**: Installs all required system packages using the appropriate package manager
- **Python Installation**: Installs Python 3.9+ with build tools if not present
- **PyQt5 Setup**: Installs complete PyQt5 GUI framework and Qt5 dependencies
- **Network Tools**: Installs network analysis and monitoring tools
- **Development Tools**: Installs text editors, version control, and system utilities

### Step 2: Additional Tools Installation
- **Network Utilities**: nmap, netcat, socat, bridge-utils, vlan
- **System Monitoring**: iotop, nethogs, iftop, htop, sysstat
- **Development Tools**: vim, nano, git, jq, yq, tree
- **Archive Tools**: zip, unzip, tar, gzip
- **Network Diagnostics**: traceroute, mtr
- **Security Tools**: SSH client/server

### Step 3: Docker Installation
- **Docker Engine**: Installs and configures Docker
- **Docker Service**: Starts and enables Docker service
- **User Permissions**: Adds user to docker group

### Step 4: Python Environment Setup
- **Virtual Environment**: Creates isolated Python environment
- **Build Dependencies**: Installs cython, numpy, cffi
- **Package Management**: Upgrades pip, setuptools, wheel

### Step 5: OSTG Package Installation
- **OSTG Installation**: Installs OSTG wheel package
- **Additional Dependencies**: Installs psutil, requests, PyYAML, ipaddress
- **Development Tools**: Optional installation of testing and code quality tools

### Step 6: FRR Docker Container Setup
- **FRR Image**: Builds enhanced FRR Docker image
- **Network Bridge**: Creates Docker network for FRR containers
- **BGP Configuration**: Sets up BGP and routing daemon configuration

### Step 7: Systemd Services Configuration
- **Service Creation**: Creates OSTG server and client services
- **Service Enablement**: Enables automatic startup
- **Service Configuration**: Configures proper environment and working directory

### Step 8: Verification and Testing
- **Installation Verification**: Tests all installed components
- **FRR Functionality**: Verifies FRR container and routing capabilities
- **Service Testing**: Tests systemd service startup and health
- **Network Testing**: Verifies network connectivity and configuration

## Manual Installation

### 1. System Dependencies

#### Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev python3-tk python3-setuptools python3-wheel \
    build-essential git curl wget net-tools iproute2 iptables ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https pkg-config \
    libffi-dev libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libncurses5-dev libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev liblzma-dev \
    python3-pyqt5 python3-pyqt5.qtwidgets python3-pyqt5.qtcore python3-pyqt5.qtgui \
    python3-pyqt5.qtsvg python3-pyqt5.qtnetwork python3-pyqt5.qtdbus qt5-default \
    iputils-ping tcpdump wireshark-common vim nano htop tree jq unzip sysstat iotop \
    nmap netcat socat bridge-utils vlan iotop nethogs iftop traceroute mtr-tiny \
    openssh-client openssh-server
```

#### CentOS/RHEL (8+):
```bash
sudo dnf update -y
sudo dnf groupinstall -y "Development Tools"
sudo dnf install -y \
    python3 python3-pip python3-devel python3-tkinter python3-setuptools python3-wheel \
    gcc gcc-c++ make git curl wget net-tools iproute iptables ca-certificates gnupg2 \
    pkgconfig libffi-devel openssl-devel zlib-devel bzip2-devel readline-devel \
    sqlite-devel ncurses-devel xz-devel tk-devel libxml2-devel libxmlsec1-devel lzma-devel \
    python3-qt5 qt5-qtbase-devel qt5-qttools-devel \
    iputils tcpdump wireshark-cli vim nano htop tree jq unzip sysstat iotop \
    nmap-ncat socat bridge-utils vlan iotop nethogs iftop traceroute mtr \
    openssh-clients openssh-server
```

#### CentOS/RHEL (7):
```bash
sudo yum update -y
sudo yum groupinstall -y "Development Tools"
sudo yum install -y epel-release
sudo yum install -y \
    python3 python3-pip python3-devel python3-tkinter python3-setuptools python3-wheel \
    gcc gcc-c++ make git curl wget net-tools iproute iptables ca-certificates gnupg2 \
    pkgconfig libffi-devel openssl-devel zlib-devel bzip2-devel readline-devel \
    sqlite-devel ncurses-devel xz-devel tk-devel libxml2-devel libxmlsec1-devel lzma-devel \
    python3-qt5 qt5-qtbase-devel qt5-qttools-devel \
    iputils tcpdump wireshark vim nano htop tree jq unzip sysstat iotop \
    nmap nc socat bridge-utils vlan iotop nethogs iftop traceroute mtr \
    openssh-clients openssh-server
```

#### Alpine:
```bash
sudo apk update
sudo apk add \
    python3 py3-pip python3-dev python3-tkinter py3-setuptools py3-wheel \
    build-base git curl wget net-tools iproute2 iptables ca-certificates gnupg \
    pkgconfig libffi-dev openssl-dev zlib-dev bzip2-dev readline-dev \
    sqlite-dev ncurses-dev xz-dev tk-dev libxml2-dev libxmlsec1-dev lzma-dev \
    py3-pyqt5 qt5-qtbase-dev qt5-qttools-dev \
    iputils tcpdump tshark vim nano htop tree jq unzip sysstat iotop \
    nmap netcat-openbsd socat bridge-utils vlan iotop nethogs iftop traceroute mtr \
    openssh-client openssh-server
```

#### openSUSE:
```bash
sudo zypper install -y \
    python3 python3-pip python3-devel python3-tk python3-setuptools python3-wheel \
    gcc gcc-c++ make git curl wget net-tools iproute2 iptables ca-certificates gnupg2 \
    pkg-config libffi-devel openssl-devel zlib-devel bzip2-devel readline-devel \
    sqlite3-devel ncurses-devel xz-devel tk-devel libxml2-devel libxmlsec1-devel lzma-devel \
    python3-qt5 libqt5-qtbase-devel libqt5-qttools-devel \
    iputils tcpdump wireshark vim nano htop tree jq unzip sysstat iotop \
    nmap netcat socat bridge-utils vlan iotop nethogs iftop traceroute mtr \
    openssh openssh-clients
```

### 2. Install OSTG Package

```bash
# Create virtual environment
python3 -m venv ostg_env
source ostg_env/bin/activate

# Upgrade pip and install build tools
pip install --upgrade pip setuptools wheel

# Install build dependencies for compiled packages
pip install --upgrade cython numpy cffi

# Install OSTG package (includes all dependencies)
pip install ostg_trafficgen-0.1.52-py3-none-any.whl

# Install additional dependencies
pip install --upgrade psutil requests PyYAML ipaddress

# Optional: Install development tools
pip install --upgrade pytest pytest-cov black flake8 mypy
```

### 3. Install Docker + FRR

```bash
# Use the Python installer (automatically installs Docker if needed)
ostg-docker-install --project-root venv/lib/python3.10/site-packages/ostg_docker

# Or if Docker is already installed, skip Docker installation
ostg-docker-install --skip-docker --project-root venv/lib/python3.10/site-packages/ostg_docker
```

### 4. Verify Installation

```bash
# Check Docker installation
docker --version
docker images | grep ostg-frr

# Check OSTG commands
ostg-server --help
ostg-client --help
ostg-docker-install --help
```

## Docker + FRR Components

### Docker Installation

The installation includes:
- Docker Engine
- Docker Compose
- Proper configuration for privileged containers
- OSTG user added to docker group

### FRR Docker Image

- **Image**: `ostg-frr:latest`
- **Base**: Alpine Linux with FRR
- **Features**: BGP, OSPF, IS-IS support
- **Network**: Connected to `ostg-frr-network`

### Docker Network

- **Name**: `ostg-frr-network`
- **Type**: Bridge network
- **Subnet**: 172.30.0.0/16
- **Purpose**: Isolated network for FRR containers

## Configuration

### Main Configuration File

Location: `/opt/ostg/ostg.conf`

```ini
[server]
host = 0.0.0.0
port = 5050
debug = false

[docker]
enabled = true
frr_image = ostg-frr:latest
network_name = ostg-frr-network

[logging]
level = INFO
file = /var/log/ostg/server.log
```

### Environment Variables

- `OSTG_CONFIG`: Path to configuration file
- `OSTG_LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `OSTG_DOCKER_ENABLED`: Enable/disable Docker support

## Systemd Services

### Available Services

- `ostg-server.service`: OSTG server daemon
- `ostg-client.service`: OSTG client GUI
- `ostg-cleanup.timer`: Periodic cleanup service

### Service Management

```bash
# Start services
sudo systemctl start ostg-server
sudo systemctl start ostg-client

# Enable auto-start
sudo systemctl enable ostg-server
sudo systemctl enable ostg-client

# Check status
sudo systemctl status ostg-server
sudo systemctl status ostg-client

# View logs
sudo journalctl -u ostg-server -f
sudo journalctl -u ostg-client -f
```

## Usage

### Starting OSTG

#### Method 1: Command Line
```bash
# Start server
ostg-server

# Start client (in another terminal)
ostg-client
```

#### Method 2: Systemd Services
```bash
# Start server service
sudo systemctl start ostg-server

# Start client service
sudo systemctl start ostg-client
```

### Using FRR Docker Containers

When you add devices through the OSTG client, FRR Docker containers are automatically created with:
- BGP support
- OSPF support
- Network isolation
- Automatic configuration

### Testing Device Launch with Docker FRR

To test that device launch with Docker FRR is working correctly:

```bash
# 1. Start OSTG server
ostg-server --host 0.0.0.0 --port 5051 &

# 2. Create a device (this will automatically launch FRR container)
curl -X POST http://localhost:5051/api/device/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "interface": "eth0",
    "ipv4": "192.168.1.100",
    "ipv4_mask": "24",
    "device_id": "test-device-001",
    "device_name": "test-device"
  }'

# 3. Check if FRR container is running
docker ps | grep ostg-frr

# 4. Check container logs
docker logs ostg-frr-test-device-001

# 5. Check FRR status
curl -X GET http://localhost:5051/api/frr/status
```

Expected results:
- Device creation returns: `{"interface":"eth0","status":"configured"}`
- FRR container shows as "Up" and "healthy"
- Container logs show FRR daemons started successfully
- BGP functionality is ready for configuration

### Docker Management

```bash
# List OSTG containers
docker ps -f name=ostg-frr

# View container logs
docker logs <container-name>

# Stop containers
docker stop $(docker ps -q -f name=ostg-frr)

# Remove containers
docker rm $(docker ps -aq -f name=ostg-frr)
```

## Troubleshooting

### Docker Issues

1. **Permission denied**:
   ```bash
   sudo usermod -aG docker $USER
   # Log out and back in
   ```

2. **Docker daemon not running**:
   ```bash
   sudo systemctl start docker
   sudo systemctl enable docker
   ```

3. **FRR containers not starting**:
   ```bash
   # Check Docker logs
   docker logs <container-name>
   
   # Rebuild FRR image
   ostg-docker-install --skip-docker
   ```

### OSTG Issues

1. **Server not starting**:
   ```bash
   # Check logs
   tail -f /var/log/ostg/server.log
   
   # Check configuration
   ostg-server --config-check
   ```

2. **Client GUI issues**:
   ```bash
   # Check display
   echo $DISPLAY
   
   # Run with debug
   ostg-client --debug
   ```

### Network Issues

1. **Port conflicts**:
   ```bash
   # Check port usage
   netstat -tlnp | grep 5050
   
   # Change port in config
   # Edit /opt/ostg/ostg.conf
   ```

2. **Firewall issues**:
   ```bash
   # Allow OSTG port
   sudo ufw allow 5050
   # or
   sudo firewall-cmd --add-port=5050/tcp --permanent
   sudo firewall-cmd --reload
   ```

## Uninstallation

### Complete Removal

```bash
# Stop services
sudo systemctl stop ostg-server ostg-client
sudo systemctl disable ostg-server ostg-client

# Remove systemd files
sudo rm /etc/systemd/system/ostg-*.service
sudo rm /etc/systemd/system/ostg-*.timer
sudo systemctl daemon-reload

# Remove OSTG directory
sudo rm -rf /opt/ostg

# Remove OSTG user
sudo userdel -r ostg

# Remove Docker images (optional)
docker rmi ostg-frr:latest
docker network rm ostg-frr-network
```

### Partial Removal

To remove only OSTG but keep Docker:
```bash
# Remove OSTG package
pip uninstall ostg-trafficgen

# Remove OSTG files
sudo rm -rf /opt/ostg
sudo userdel -r ostg
```

## Support

For issues and questions:
1. Check the logs: `/var/log/ostg/`
2. Review this documentation
3. Check the GitHub issues page
4. Contact the development team

## Development

### Building from Source

```bash
# Clone repository
git clone <repository-url>
cd OSTG

# Install in development mode
pip install -e .

# Run tests
pytest

# Build package
python -m build
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes
4. Add tests
5. Submit a pull request
