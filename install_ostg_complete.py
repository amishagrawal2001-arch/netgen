#!/usr/bin/env python3
"""
Netgen Complete Installation Script (Python Version)
Installs the Netgen traffic generator with all dependencies including Docker and FRR.
"""

import os
import sys
import subprocess
import platform
import shutil
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Product identity
PRODUCT_NAME = "Netgen"
NETGEN_VERSION = "0.2.0"

# Wheel artifact still ships under its original distribution name; surface rename
# leaves the Python package internals untouched.
WHEEL_DIST = "ostg_trafficgen"
WHEEL_VERSION = "0.1.52"

PYTHON_VERSION = "3.10"
VENV_NAME = "netgen_env"
NETGEN_PORT = 5051
DOCKER_IMAGE = "netgen-frr:latest"
DOCKER_NETWORK = "netgen-frr-network"
INSTALL_DIR = "/opt/netgen"

# Legacy names retained only for clean-up of prior OSTG installs.
LEGACY_INSTALL_DIR = "/opt/OSTG"
LEGACY_DOCKER_IMAGE = "ostg-frr:latest"
LEGACY_DOCKER_NETWORK = "ostg-frr-network"
LEGACY_SYSTEMD_UNITS = [
    "ostg-server.service",
    "ostg-client.service",
    "ostg-cleanup.service",
    "ostg-cleanup.timer",
]

# Color codes for output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

class NetgenInstaller:
    def __init__(self, remote_host: Optional[str] = None, remote_user: str = "root", remote_pass: Optional[str] = None):
        self.remote_host = remote_host
        self.remote_user = remote_user
        self.remote_pass = remote_pass
        self.remote_install = remote_host is not None
        self.ostg_server_active = False
        self.docker_frr_available = False
        self.setup_logging()
        self.system_info = self._detect_system()
        
    def setup_logging(self):
        """Setup logging configuration: dedicated logger with timestamped log file so install output is captured."""
        log_name = "netgen_installer"
        self.logger = logging.getLogger(log_name)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.propagate = False

        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for base_dir in ["/tmp", os.path.expanduser("~"), "."]:
            log_dir = os.path.abspath(base_dir)
            log_path = os.path.join(log_dir, f"netgen_install_{timestamp}.log")
            try:
                fh = logging.FileHandler(log_path, encoding="utf-8")
                fh.setFormatter(fmt)
                self.logger.addHandler(fh)
                self.install_log_path = log_path
                break
            except (OSError, IOError):
                continue
        else:
            self.install_log_path = None  # no writable path found
        
    def log(self, message: str, level: str = "INFO"):
        """Log message with color coding"""
        color_map = {
            "INFO": Colors.GREEN,
            "WARNING": Colors.YELLOW,
            "ERROR": Colors.RED,
            "DEBUG": Colors.BLUE
        }
        color = color_map.get(level, Colors.NC)
        formatted_msg = f"{color}[{level}]{Colors.NC} {message}"
        print(formatted_msg)
        getattr(self.logger, level.lower(), self.logger.info)(message)
        
    def _detect_system(self) -> Dict[str, str]:
        """Detect the operating system and package manager"""
        system_info = {
            "os": "unknown",
            "distro": None,
            "package_manager": None,
            "python_cmd": None
        }
        
        if self.remote_install:
            # Detect system on remote host
            try:
                # Detect OS release
                result = self.run_command("cat /etc/os-release", capture_output=True)
                if result.returncode == 0:
                    content = result.stdout.lower()
                    if "ubuntu" in content:
                        system_info["distro"] = "ubuntu"
                        system_info["package_manager"] = "apt"
                    elif "centos" in content or "rhel" in content:
                        system_info["distro"] = "centos"
                        system_info["package_manager"] = "yum"
                    elif "fedora" in content:
                        system_info["distro"] = "fedora"
                        system_info["package_manager"] = "dnf"
                    elif "alpine" in content:
                        system_info["distro"] = "alpine"
                        system_info["package_manager"] = "apk"
                    elif "suse" in content:
                        system_info["distro"] = "suse"
                        system_info["package_manager"] = "zypper"
                
                # Detect Python command
                for python_cmd in ["python3.10", "python3", "python"]:
                    result = self.run_command(f"which {python_cmd}", check=False, capture_output=True)
                    if result.returncode == 0:
                        system_info["python_cmd"] = python_cmd
                        break
                        
            except Exception as e:
                self.log(f"Error detecting remote system: {e}", "ERROR")
                
        else:
            # Detect system locally
            system_info["os"] = platform.system().lower()
            
            # Detect distribution
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r") as f:
                    content = f.read()
                    if "ubuntu" in content.lower():
                        system_info["distro"] = "ubuntu"
                        system_info["package_manager"] = "apt"
                    elif "centos" in content.lower() or "rhel" in content.lower():
                        system_info["distro"] = "centos"
                        system_info["package_manager"] = "yum"
                    elif "fedora" in content.lower():
                        system_info["distro"] = "fedora"
                        system_info["package_manager"] = "dnf"
                    elif "alpine" in content.lower():
                        system_info["distro"] = "alpine"
                        system_info["package_manager"] = "apk"
                    elif "suse" in content.lower():
                        system_info["distro"] = "suse"
                        system_info["package_manager"] = "zypper"
            
            # Detect Python command
            for python_cmd in ["python3.10", "python3", "python"]:
                if shutil.which(python_cmd):
                    system_info["python_cmd"] = python_cmd
                    break
                
        return system_info
        
    def run_command(self, command: str, check: bool = True, capture_output: bool = False, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run a command locally or remotely"""
        # Set environment variables for non-interactive installation
        env = os.environ.copy()
        env.update({
            'DEBIAN_FRONTEND': 'noninteractive',
            'DEBIAN_PRIORITY': 'critical',
            'TERM': 'dumb',
            'UCF_FORCE_CONFFNEW': '1'
        })
        
        if self.remote_install:
            # Run command on remote host with only necessary environment variables
            essential_env_vars = [
                'DEBIAN_FRONTEND=noninteractive',
                'DEBIAN_PRIORITY=critical', 
                'TERM=dumb',
                'UCF_FORCE_CONFFNEW=1'
            ]
            env_vars = ' '.join(essential_env_vars)
            ssh_cmd = f"sshpass -p '{self.remote_pass}' ssh {self.remote_user}@{self.remote_host} '{env_vars} {command}'"
            return subprocess.run(ssh_cmd, shell=True, check=check, capture_output=capture_output, text=True, timeout=timeout)
        else:
            # Run command locally
            return subprocess.run(command, shell=True, check=check, capture_output=capture_output, text=True, env=env, timeout=timeout)
            
    def copy_file(self, local_path: str, remote_path: str):
        """Copy file to remote host"""
        if self.remote_install:
            subprocess.run(f"sshpass -p '{self.remote_pass}' scp {local_path} {self.remote_user}@{self.remote_host}:{remote_path}", 
                          shell=True, check=True)
        else:
            shutil.copy2(local_path, remote_path)
            
    def install_system_dependencies(self):
        """Install system dependencies based on the detected OS"""
        self.log("Installing system dependencies...")
        
        try:
            if self.system_info["package_manager"] == "apt":
                self._install_apt_packages()
            elif self.system_info["package_manager"] == "dnf":
                self._install_dnf_packages()
            elif self.system_info["package_manager"] == "yum":
                self._install_yum_packages()
            elif self.system_info["package_manager"] == "apk":
                self._install_apk_packages()
            elif self.system_info["package_manager"] == "zypper":
                self._install_zypper_packages()
            else:
                self.log(f"Unsupported package manager: {self.system_info['package_manager']}", "ERROR")
                sys.exit(1)
        except Exception as e:
            self.log(f"System dependencies installation encountered issues: {e}", "WARNING")
            self.log("Attempting to continue with remaining installation steps...", "WARNING")
            # Try to fix broken packages using the appropriate package manager
            if self.system_info["package_manager"] == "apt":
                self.run_command("apt-get --fix-broken install -y", check=False)
            elif self.system_info["package_manager"] == "dnf":
                self.run_command("dnf check", check=False)
            elif self.system_info["package_manager"] == "yum":
                self.run_command("yum check", check=False)
            elif self.system_info["package_manager"] == "apk":
                self.run_command("apk fix", check=False)
            elif self.system_info["package_manager"] == "zypper":
                self.run_command("zypper verify", check=False)
            # Don't exit - allow installation to continue
            
    def _install_apt_packages(self):
        """Install packages using apt"""
        packages = [
            "python3", "python3-pip", "python3-venv", "python3-dev", "python3-tk",
            "python3-setuptools", "python3-wheel", "build-essential", "git", "curl", "wget",
            "net-tools", "iproute2", "iptables", "ca-certificates", "gnupg", "lsb-release",
            "software-properties-common", "apt-transport-https", "pkg-config", "libffi-dev",
            "libssl-dev", "zlib1g-dev", "libbz2-dev", "libreadline-dev", "libsqlite3-dev",
            "libncurses5-dev", "libncursesw5-dev", "xz-utils", "tk-dev", "libxml2-dev",
            "libxmlsec1-dev", "liblzma-dev", "iputils-ping", "tcpdump", "wireshark-common",
            "vim", "nano", "htop", "tree", "jq", "unzip", "sysstat", "iotop",
            "nmap", "netcat", "socat", "bridge-utils", "vlan", "nethogs", "iftop",
            "yq", "zip", "tar", "gzip", "traceroute", "mtr-tiny", "openssh-client", "openssh-server"
        ]
        
        # Wait for any existing apt processes to finish
        self._wait_for_apt_lock()
        
        # Remove duplicates and filter out packages that might not exist
        packages = list(set(packages))
        packages_to_install = []
        
        for package in packages:
            # Check if package exists
            result = self.run_command(f"apt-cache show {package}", check=False, capture_output=True)
            if result.returncode == 0:
                packages_to_install.append(package)
            else:
                self.log(f"Package {package} not available, skipping", "WARNING")
        
        if packages_to_install:
            # Pre-configure packages to avoid interactive prompts
            self._preconfigure_packages()
            
            # Fix GPG keys before updating package lists
            self._fix_apt_gpg_keys()
            
            try:
                update_result = self.run_command(f"apt-get update", check=False)
                if update_result.returncode != 0:
                    self.log("apt-get update had some issues, but continuing...", "WARNING")
                self.run_command(f"apt-get install -y {' '.join(packages_to_install)}")
            except subprocess.CalledProcessError as e:
                self.log(f"Package installation encountered issues: {e}", "WARNING")
                # Try to fix broken packages
                fix_result = self.run_command("apt-get --fix-broken install -y", check=False)
                if fix_result.returncode != 0:
                    self.log("Some packages may have dependency issues (e.g., NVIDIA drivers)", "WARNING")
                    self.log("This is usually non-critical. Continuing with installation...", "WARNING")
                # Try installation again, but don't fail if it still has issues
                retry_result = self.run_command(f"apt-get install -y {' '.join(packages_to_install)}", check=False)
                if retry_result.returncode != 0:
                    self.log("Some system packages could not be installed due to dependency conflicts", "WARNING")
                    self.log("This may be due to NVIDIA driver conflicts. Continuing with OSTG installation...", "WARNING")
                
    def _wait_for_apt_lock(self):
        """Wait for any existing apt processes to finish"""
        import time
        max_wait = 300  # 5 minutes
        wait_time = 0
        
        while wait_time < max_wait:
            result = self.run_command("pgrep -f '(apt|dpkg)'", check=False, capture_output=True)
            if result.returncode != 0 or not result.stdout.strip():
                self.log("✓ No conflicting apt processes found")
                return
                
            self.log(f"Waiting for apt processes to finish... ({wait_time}s)")
            time.sleep(10)
            wait_time += 10
            
        self.log("Timeout waiting for apt processes. Proceeding anyway.", "WARNING")
            
    def _preconfigure_packages(self):
        """Pre-configure packages to avoid interactive prompts"""
        preconfig_commands = [
            # Configure Wireshark to allow non-superusers to capture packets
            "echo 'wireshark-common wireshark-common/install-setuid boolean true' | debconf-set-selections",
            # Configure other packages as needed
            "echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections",
            "echo 'debconf debconf/priority select critical' | debconf-set-selections"
        ]
        
        for cmd in preconfig_commands:
            self.run_command(cmd, check=False)
            
    def _install_dnf_packages(self):
        """Install packages using dnf"""
        self.run_command("dnf update -y")
        self.run_command("dnf groupinstall -y 'Development Tools'")
        
        packages = [
            "python3", "python3-pip", "python3-devel", "python3-tkinter", "python3-setuptools",
            "python3-wheel", "gcc", "gcc-c++", "make", "git", "curl", "wget", "net-tools",
            "iproute", "iptables", "ca-certificates", "gnupg2", "pkgconfig", "libffi-devel",
            "openssl-devel", "zlib-devel", "bzip2-devel", "readline-devel", "sqlite-devel",
            "ncurses-devel", "xz-devel", "tk-devel", "libxml2-devel", "libxmlsec1-devel",
            "lzma-devel", "nmap-ncat", "socat", "bridge-utils", "vlan", "iotop", "nethogs",
            "iftop", "jq", "yq", "zip", "unzip", "tar", "gzip", "htop", "tree", "traceroute",
            "mtr", "vim", "nano", "git", "openssh-clients", "openssh-server"
        ]
        
        self.run_command(f"dnf install -y {' '.join(packages)}")
        
    def _install_yum_packages(self):
        """Install packages using yum"""
        self.run_command("yum update -y")
        self.run_command("yum groupinstall -y 'Development Tools'")
        self.run_command("yum install -y epel-release")
        
        packages = [
            "python3", "python3-pip", "python3-devel", "python3-tkinter", "python3-setuptools",
            "python3-wheel", "gcc", "gcc-c++", "make", "git", "curl", "wget", "net-tools",
            "iproute", "iptables", "ca-certificates", "gnupg2", "pkgconfig", "libffi-devel",
            "openssl-devel", "zlib-devel", "bzip2-devel", "readline-devel", "sqlite-devel",
            "ncurses-devel", "xz-devel", "tk-devel", "libxml2-devel", "libxmlsec1-devel",
            "lzma-devel", "nmap", "nc", "socat", "bridge-utils", "vlan", "iotop", "nethogs",
            "iftop", "jq", "zip", "unzip", "tar", "gzip", "htop", "tree", "traceroute",
            "mtr", "vim", "nano", "git", "openssh-clients", "openssh-server"
        ]
        
        self.run_command(f"yum install -y {' '.join(packages)}")
        
    def _install_apk_packages(self):
        """Install packages using apk"""
        packages = [
            "python3", "python3-dev", "py3-pip", "build-base", "git", "curl", "wget",
            "iptables", "ca-certificates", "pkgconfig", "libffi-dev", "openssl-dev",
            "zlib-dev", "bzip2-dev", "readline-dev", "sqlite-dev", "ncurses-dev",
            "xz-dev", "tk-dev", "libxml2-dev", "libxmlsec1-dev", "lzma-dev", "nmap",
            "netcat-openbsd", "socat", "bridge-utils", "vlan", "iotop", "htop",
            "tree", "vim", "nano", "git", "openssh-client", "openssh-server"
        ]
        
        self.run_command(f"apk add {' '.join(packages)}")
        
    def _install_zypper_packages(self):
        """Install packages using zypper"""
        packages = [
            "python3", "python3-pip", "python3-devel", "python3-tk", "python3-setuptools",
            "python3-wheel", "gcc", "gcc-c++", "make", "git", "curl", "wget", "net-tools",
            "iproute2", "iptables", "ca-certificates", "gnupg2", "pkgconfig", "libffi-devel",
            "openssl-devel", "zlib-devel", "bzip2-devel", "readline-devel", "sqlite3-devel",
            "ncurses-devel", "xz-devel", "tk-devel", "libxml2-devel", "libxmlsec1-devel",
            "lzma-devel", "nmap", "netcat", "socat", "bridge-utils", "vlan", "iotop",
            "htop", "tree", "vim", "nano", "git", "openssh"
        ]
        
        self.run_command(f"zypper install -y {' '.join(packages)}")
        
    def install_python_dependencies(self):
        """Install Python build dependencies and Python 3.10 if needed"""
        self.log("Installing Python dependencies...")
        
        # Check if Python 3.10 is available
        result = self.run_command("python3.10 --version", check=False, capture_output=True)
        if result.returncode == 0:
            self.log(f"✓ Python 3.10 already installed: {result.stdout.strip()}")
            return
            
        # Try to install Python 3.10
        if self.system_info["package_manager"] == "apt":
            # Fix GPG keys before updating
            self._fix_apt_gpg_keys()
            update_result = self.run_command("apt-get update", check=False)
            if update_result.returncode != 0:
                self.log("apt-get update had some issues, but continuing...", "WARNING")
            self.run_command("apt-get install -y software-properties-common")
            self.run_command("add-apt-repository -y ppa:deadsnakes/ppa")
            update_result = self.run_command("apt-get update", check=False)
            if update_result.returncode != 0:
                self.log("apt-get update had some issues after adding PPA, but continuing...", "WARNING")
            self.run_command("apt-get install -y python3.10 python3.10-venv python3.10-dev python3.10-distutils")
            self.run_command("curl -sS https://bootstrap.pypa.io/get-pip.py | python3.10")
        elif self.system_info["package_manager"] == "dnf":
            self.run_command("dnf install -y python3.10 python3.10-pip python3.10-devel")
        elif self.system_info["package_manager"] == "yum":
            self.run_command("yum install -y epel-release")
            self.run_command("yum install -y python3.10 python3.10-pip python3.10-devel")
        elif self.system_info["package_manager"] == "apk":
            self.run_command("apk add python3.10 python3.10-dev py3.10-pip")
        elif self.system_info["package_manager"] == "zypper":
            self.run_command("zypper install -y python3.10 python3.10-pip python3.10-devel")
            
        # Verify installation
        result = self.run_command("python3.10 --version", check=False, capture_output=True)
        if result.returncode == 0:
            self.log(f"✓ Python 3.10 installed successfully: {result.stdout.strip()}")
        else:
            self.log("Failed to install Python 3.10", "ERROR")
            sys.exit(1)
            
    def _fix_apt_gpg_keys(self):
        """Fix missing GPG keys for apt repositories"""
        self.log("Checking and fixing GPG keys for apt repositories...")
        
        # Check if InfluxData repository exists
        result = self.run_command("grep -r 'repos.influxdata.com' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null", check=False, capture_output=True)
        if result.returncode == 0 and "influxdata" in result.stdout.lower():
            self.log("InfluxData repository detected, adding GPG key...")
            # Add InfluxData GPG key using modern method (preferred for Ubuntu 22.04+)
            self.run_command("mkdir -p /etc/apt/keyrings", check=False)
            influx_key_cmd = "curl -fsSL https://repos.influxdata.com/influxdb.key | gpg --dearmor -o /etc/apt/keyrings/influxdb.gpg"
            key_result = self.run_command(influx_key_cmd, check=False)
            if key_result.returncode == 0:
                self.log("✓ InfluxData GPG key added successfully")
            else:
                # Try legacy method as fallback (for older Ubuntu versions)
                self.log("Trying legacy method for InfluxData GPG key...", "WARNING")
                legacy_cmd = "curl -fsSL https://repos.influxdata.com/influxdb.key | apt-key add -"
                self.run_command(legacy_cmd, check=False)
        
        # Try apt-get update to detect any missing keys
        update_result = self.run_command("apt-get update 2>&1", check=False, capture_output=True)
        if update_result.returncode != 0 and "NO_PUBKEY" in update_result.stdout:
            import re
            # Extract missing key IDs
            key_ids = re.findall(r'NO_PUBKEY\s+([A-F0-9]+)', update_result.stdout)
            for key_id in key_ids:
                self.log(f"Adding missing GPG key: {key_id}")
                # Try to get the key from keyserver using multiple methods
                # Method 1: Modern gpg with keyring
                self.run_command(f"gpg --no-default-keyring --keyring /tmp/tmp-keyring.gpg --keyserver keyserver.ubuntu.com --recv-keys {key_id} && gpg --no-default-keyring --keyring /tmp/tmp-keyring.gpg --export --output /etc/apt/keyrings/{key_id}.gpg {key_id}", check=False)
                # Method 2: Legacy apt-key (for older systems)
                self.run_command(f"apt-key adv --keyserver keyserver.ubuntu.com --recv-keys {key_id}", check=False)
                # Method 3: Direct gpg import
                gpg_cmd = f"gpg --keyserver keyserver.ubuntu.com --recv-keys {key_id} 2>/dev/null && gpg --export --armor {key_id} 2>/dev/null | apt-key add - 2>/dev/null"
                self.run_command(gpg_cmd, check=False)
    
    def install_docker(self):
        """Install Docker"""
        self.log("Installing Docker...")
        
        # Check if Docker is already installed
        result = self.run_command("docker --version", check=False, capture_output=True)
        if result.returncode == 0:
            self.log(f"✓ Docker already installed: {result.stdout.strip()}")
            return
            
        if self.system_info["package_manager"] == "apt":
            # Ubuntu/Debian Docker installation
            # Fix GPG keys before updating
            self._fix_apt_gpg_keys()
            # Try apt-get update, but continue even if there are warnings
            update_result = self.run_command("apt-get update", check=False)
            if update_result.returncode != 0:
                self.log("apt-get update had some issues, but continuing...", "WARNING")
                # Try to fix GPG keys again and retry
                self._fix_apt_gpg_keys()
                self.run_command("apt-get update", check=False)
            self.run_command("apt-get install -y ca-certificates curl gnupg lsb-release")
            self.run_command("mkdir -p /etc/apt/keyrings")
            self.run_command("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
            self.run_command('echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null')
            self.run_command("apt-get update")
            self.run_command("apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
        elif self.system_info["package_manager"] == "dnf":
            # Fedora Docker installation
            self.run_command("dnf install -y dnf-plugins-core")
            self.run_command("dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo")
            self.run_command("dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
        elif self.system_info["package_manager"] == "yum":
            # CentOS/RHEL Docker installation
            self.run_command("yum install -y yum-utils")
            self.run_command("yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo")
            self.run_command("yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
        elif self.system_info["package_manager"] == "apk":
            # Alpine Docker installation
            self.run_command("apk add docker docker-compose")
            
        # Start and enable Docker
        self.run_command("systemctl start docker")
        self.run_command("systemctl enable docker")
        
        # Add current user to docker group (if not root)
        if self.remote_install:
            self.run_command("usermod -aG docker root")
        else:
            current_user = os.getenv("USER")
            if current_user and current_user != "root":
                self.run_command(f"usermod -aG docker {current_user}")
                self.log("Added user to docker group. You may need to log out and back in.", "WARNING")
                
        self.log("✓ Docker installed and started successfully")
        
    def install_ostg(self):
        """Install the Netgen traffic generator wheel and ancillary files."""
        self.log(f"Installing {PRODUCT_NAME} traffic generator...")

        # Create installation directory
        self.run_command(f"mkdir -p {INSTALL_DIR}")

        # Copy wheel file (still distributed under its original name)
        wheel_file = f"{WHEEL_DIST}-{WHEEL_VERSION}-py3-none-any.whl"
        local_wheel_path = f"dist/{wheel_file}"
        remote_wheel_path = f"{INSTALL_DIR}/{wheel_file}"
        
        if not os.path.exists(local_wheel_path):
            self.log(f"Wheel file not found: {local_wheel_path}", "ERROR")
            sys.exit(1)
            
        self.copy_file(local_wheel_path, remote_wheel_path)
        
        # Install wheel (retry with --ignore-installed if distutils-owned packages block uninstall)
        pip_result = self.run_command(f"pip3 install {remote_wheel_path}", check=False, capture_output=True)
        if pip_result.returncode != 0:
            err = (pip_result.stderr or "") + (pip_result.stdout or "")
            if "uninstall-distutils-installed-package" in err or "Cannot uninstall" in err:
                self.log("Pip failed due to distutils-installed package conflict; retrying with --ignore-installed", "WARNING")
                pip_result = self.run_command(f"pip3 install --ignore-installed {remote_wheel_path}", check=False, capture_output=True)
            if pip_result.returncode != 0:
                self.log(f"Wheel install failed: {pip_result.stderr or pip_result.stdout or 'unknown'}", "ERROR")
                raise SystemExit(1)
        
        # Copy FRR Docker files: prefer root Dockerfile.frr (Alpine-based, no apt-get build)
        # to avoid apt-get failures on remote servers (libyang2-dev, python3.11-dev, etc.)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root_dockerfile = os.path.join(script_dir, "Dockerfile.frr")
        ostg_docker_dockerfile = os.path.join(script_dir, "ostg_docker", "Dockerfile.frr")
        dockerfile_src = root_dockerfile if os.path.exists(root_dockerfile) else ostg_docker_dockerfile
        if dockerfile_src == root_dockerfile:
            self.log("Using Alpine-based FRR Dockerfile (avoids apt-get build failures on server)")
        files_to_copy = [
            (dockerfile_src, f"{INSTALL_DIR}/Dockerfile.frr"),
            (os.path.join(script_dir, "ostg_docker", "frr.conf.template"), f"{INSTALL_DIR}/frr.conf.template"),
            (os.path.join(script_dir, "ostg_docker", "start-frr.sh"), f"{INSTALL_DIR}/start-frr.sh"),
        ]

        for local_file, remote_file in files_to_copy:
            if os.path.exists(local_file):
                self.copy_file(local_file, remote_file)
                if remote_file.endswith(".sh"):
                    self.run_command(f"chmod +x {remote_file}")

        # DPDK runtime scripts. The server endpoints (/api/dpdk/{status,bind,unbind,
        # interfaces}) shell out to dpdk_bind.sh from $INSTALL_DIR/resources/dpdk/;
        # if the directory is missing they return 404 "dpdk_bind.sh not found".
        # Ship the whole resources/dpdk/*.sh tree so DPDK actions work out of the box.
        dpdk_src_dir = os.path.join(script_dir, "resources", "dpdk")
        dpdk_remote_dir = f"{INSTALL_DIR}/resources/dpdk"
        if os.path.isdir(dpdk_src_dir):
            self.run_command(f"mkdir -p {dpdk_remote_dir}")
            for fname in sorted(os.listdir(dpdk_src_dir)):
                if not fname.endswith(".sh"):
                    continue
                local_sh = os.path.join(dpdk_src_dir, fname)
                remote_sh = f"{dpdk_remote_dir}/{fname}"
                self.copy_file(local_sh, remote_sh)
                self.run_command(f"chmod +x {remote_sh}")
            self.log(f"✓ DPDK runtime scripts deployed to {dpdk_remote_dir}")
        else:
            self.log(
                f"resources/dpdk/ not found at {dpdk_src_dir}; DPDK bind/unbind "
                "endpoints will return 404 until you deploy these manually.",
                "WARNING",
            )

        self.log(f"✓ {PRODUCT_NAME} installed successfully")
        
    def install_ai_dependencies(self):
        """Install AI/ML dependencies for AI-powered features"""
        self.log("Installing AI dependencies...")
        
        # AI/ML packages required for AI features
        ai_packages = [
            "scikit-learn>=1.3.0",
            "pandas>=2.0.0",
            "numpy>=1.24.0"
        ]
        
        # Install AI dependencies
        for package in ai_packages:
            try:
                self.run_command(f"pip3 install {package}")
                self.log(f"✓ Installed {package}")
            except subprocess.CalledProcessError as e:
                self.log(f"Failed to install {package}: {e}", "WARNING")
                # Continue with other packages even if one fails
                continue
        
        # Create AI directories
        self.run_command(f"mkdir -p {INSTALL_DIR}/ai_models")
        self.log("✓ AI dependencies installed successfully")
    
    def install_ollama(self):
        """Install Ollama for local LLM support"""
        self.log("Installing Ollama (local LLM)...")
        
        try:
            # Check if Ollama is already installed
            result = self.run_command("ollama --version", check=False, capture_output=True)
            if result.returncode == 0:
                self.log(f"✓ Ollama already installed: {result.stdout.strip()}")
                # Check if Ollama service is running
                service_result = self.run_command("systemctl is-active ollama", check=False, capture_output=True)
                if service_result.returncode == 0 and "active" in service_result.stdout:
                    self.log("✓ Ollama service is running")
                else:
                    self.log("Starting Ollama service...")
                    self.run_command("systemctl start ollama", check=False)
                    self.run_command("systemctl enable ollama", check=False)
                
                # Check if essential models are installed
                self.log("Checking for essential LLM models...")
                essential_models = ["llama3.2:latest", "all-minilm:latest", "llama2"]
                list_result = self.run_command("ollama list", check=False, capture_output=True)
                installed_models = []
                if list_result.returncode == 0:
                    # Parse installed models from output
                    for line in list_result.stdout.split('\n')[1:]:  # Skip header
                        if line.strip():
                            model_name = line.split()[0] if line.split() else ""
                            if model_name:
                                installed_models.append(model_name)
                
                # Install missing essential models
                for model in essential_models:
                    if model not in installed_models:
                        self.log(f"Installing missing model: {model}...")
                        pull_result = self.run_command(f"ollama pull {model}", check=False, timeout=600)
                        if pull_result.returncode == 0:
                            self.log(f"✓ {model} installed successfully")
                        else:
                            self.log(f"⚠ Could not install {model} (optional)", "WARNING")
                    else:
                        self.log(f"✓ {model} already installed")
                
                return
            
            # Install Ollama using official install script
            self.log("Downloading and installing Ollama...")
            install_script = "curl -fsSL https://ollama.ai/install.sh"
            
            if self.remote_install:
                # For remote install, download and execute via SSH
                self.run_command(f"{install_script} | sh")
            else:
                # For local install, download and execute
                self.run_command(f"{install_script} | sh")
            
            # Start and enable Ollama service
            self.log("Starting Ollama service...")
            self.run_command("systemctl start ollama", check=False)
            self.run_command("systemctl enable ollama", check=False)
            
            # Wait a moment for service to start
            import time
            time.sleep(2)
            
            # Verify Ollama is running
            verify_result = self.run_command("ollama --version", check=False, capture_output=True)
            if verify_result.returncode == 0:
                self.log(f"✓ Ollama installed successfully: {verify_result.stdout.strip()}")
                
                # Pull essential LLM models that the app uses
                # These models are referenced in the app's preferred models list
                essential_models = [
                    "llama3.2:latest",  # Primary model: 2GB, fast, good quality
                    "all-minilm:latest", # Very fast fallback: 45MB (if available)
                    "llama2"             # Fallback model
                ]
                
                self.log("Pulling essential LLM models for OSTG AI features...")
                self.log("This may take several minutes depending on your internet connection...")
                
                for model in essential_models:
                    self.log(f"Downloading {model}...")
                    pull_result = self.run_command(f"ollama pull {model}", check=False, timeout=600)
                    if pull_result.returncode == 0:
                        self.log(f"✓ {model} downloaded successfully")
                    else:
                        self.log(f"⚠ Could not download {model} (this is optional)", "WARNING")
                
                # Inform about optional larger models
                self.log("")
                self.log("Optional models (for better quality, install manually if needed):", "INFO")
                self.log("  - llama3.3:latest (42GB) - Large, high quality", "INFO")
                self.log("  - gemma3:27b (17GB) - Medium-large, general purpose", "INFO")
                self.log("  - qwen2.5-coder:32b (19GB) - Large, code-focused", "INFO")
                self.log("  Install with: ollama pull <model-name>", "INFO")
            else:
                self.log("⚠ Ollama installation completed but verification failed. Service may need manual start.", "WARNING")
                
        except Exception as e:
            self.log(f"Failed to install Ollama: {e}", "WARNING")
            self.log("Ollama is optional. Test plan generation will use template-based generation instead.", "WARNING")
            self.log("To install Ollama manually: curl -fsSL https://ollama.ai/install.sh | sh", "INFO")
        
    def setup_docker_frr(self):
        """Setup Docker FRR image (uses Alpine-based Dockerfile when available to avoid apt-get build failures)"""
        self.log("Setting up Docker FRR image...")
        
        dockerfile_path = f"{INSTALL_DIR}/Dockerfile.frr"
        build_context = INSTALL_DIR
        buildx_check = self.run_command("docker buildx version", check=False, capture_output=True)
        use_buildx = buildx_check.returncode == 0

        # Alternate Alpine mirrors when default CDN has "temporary error (try again later)"
        alpine_mirrors = [
            None,  # default (dl-cdn.alpinelinux.org)
            "https://ftp.halifax.rwth-aachen.de/alpine",
            "https://mirror.accum.se/mirror/alpinelinux.org",
        ]

        def run_build(mirror_arg: Optional[str] = None) -> subprocess.CompletedProcess:
            extra = f" --build-arg ALPINE_MIRROR={mirror_arg}" if mirror_arg else ""
            if use_buildx:
                return self.run_command(
                    f"docker buildx build --platform linux/amd64 -t {DOCKER_IMAGE} -f {dockerfile_path} --load{extra} {build_context}",
                    check=False, timeout=600
                )
            return self.run_command(
                f"docker build -t {DOCKER_IMAGE} -f {dockerfile_path}{extra} {build_context}",
                check=False, timeout=600
            )

        result = None
        for idx, mirror in enumerate(alpine_mirrors):
            if use_buildx and idx == 0:
                self.log("Using docker buildx for platform-specific build...")
            elif not use_buildx and idx == 0:
                self.log("Using standard docker build...")
            if mirror:
                self.log(f"Retrying with Alpine mirror: {mirror}", "INFO")
            result = run_build(mirror)
            if result.returncode == 0:
                break
            if idx < len(alpine_mirrors) - 1:
                self.log("Build failed, trying next mirror...", "WARNING")

        if result and result.returncode != 0:
            self.log("FRR Docker image build failed. OSTG will work but BGP/OSPF/ISIS device containers will be unavailable.", "WARNING")
            self.log("To fix later (with optional mirror if CDN is unreachable):", "WARNING")
            self.log(f"  docker build -t {DOCKER_IMAGE} -f {dockerfile_path} {build_context}", "WARNING")
            self.log("  Or: docker build --build-arg ALPINE_MIRROR=https://ftp.halifax.rwth-aachen.de/alpine -t ostg-frr:latest -f /opt/OSTG/Dockerfile.frr /opt/OSTG", "WARNING")
        else:
            self.log("✓ Docker FRR image built successfully")
        
        # Create Docker network
        net_result = self.run_command(f"docker network create {DOCKER_NETWORK}", check=False)
        if net_result.returncode != 0:
            self.log(f"Docker network {DOCKER_NETWORK} may already exist", "WARNING")
            
        self.log("✓ Docker FRR setup completed")
        
    def create_systemd_services(self):
        """Create systemd services"""
        self.log("Creating systemd services...")
        
        # Resolve the server entry-point dynamically. The console-script
        # name is still `ostg-server` (wheel internals weren't renamed in the
        # surface rebrand), but where it lives on disk varies by install.
        ostg_server_cmd = "/usr/local/bin/ostg-server"
        which_result = self.run_command("command -v ostg-server", check=False, capture_output=True)
        if which_result.returncode == 0 and (which_result.stdout or "").strip():
            ostg_server_cmd = (which_result.stdout or "").strip()
            self.log(f"Using ostg-server at: {ostg_server_cmd}")
        else:
            # Fallback: run module via python (e.g. if scripts not on PATH)
            ostg_server_cmd = "/usr/bin/python3 -m run_tgen_server"

        # Netgen Server Service
        server_service = f"""[Unit]
Description={PRODUCT_NAME} Traffic Generator Server
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={ostg_server_cmd}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=netgen-server

[Install]
WantedBy=multi-user.target
"""

        self.run_command(f"cat > /etc/systemd/system/netgen-server.service << 'EOF'\n{server_service}EOF")

        # Netgen Client Service
        client_service = f"""[Unit]
Description={PRODUCT_NAME} Traffic Generator Client
After=network.target netgen-server.service
Requires=netgen-server.service

[Service]
Type=simple
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 /usr/local/lib/python3.10/dist-packages/run_tgen_client.py

[Install]
WantedBy=multi-user.target
"""

        self.run_command(f"cat > /etc/systemd/system/netgen-client.service << 'EOF'\n{client_service}EOF")

        # Cleanup Service
        cleanup_service = f"""[Unit]
Description={PRODUCT_NAME} Cleanup Service
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 -c "from utils.frr_docker import cleanup_all_containers; cleanup_all_containers()"
"""

        self.run_command(f"cat > /etc/systemd/system/netgen-cleanup.service << 'EOF'\n{cleanup_service}EOF")

        # Cleanup Timer
        cleanup_timer = f"""[Unit]
Description=Run {PRODUCT_NAME} Cleanup Service every 5 minutes
Requires=netgen-cleanup.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
"""

        self.run_command(f"cat > /etc/systemd/system/netgen-cleanup.timer << 'EOF'\n{cleanup_timer}EOF")

        # Reload systemd and enable services
        self.run_command("systemctl daemon-reload")
        self.run_command("systemctl enable netgen-server.service")
        self.run_command("systemctl enable netgen-cleanup.timer")

        self.log("✓ Systemd services created successfully")
        
    def start_ostg_services(self):
        """Start Netgen services."""
        self.log(f"Starting {PRODUCT_NAME} services...")

        # Stop any existing processes
        self.run_command("pkill -f run_tgen_server.py", check=False)

        # Start the server (don't abort the install if it fails — surface a warning
        # and capture journal logs so the user can debug)
        start_result = self.run_command("systemctl start netgen-server.service", check=False)
        if start_result.returncode != 0:
            self.log("systemctl start netgen-server returned non-zero; checking status...", "WARNING")

        # Give the server a moment to start (or fail)
        self.run_command("sleep 3", check=False)

        # Check status — use check=False so the install script does not abort
        result = self.run_command(
            "systemctl is-active netgen-server.service", check=False, capture_output=True
        )
        if result.returncode == 0 and (result.stdout or "").strip() == "active":
            self.log(f"✓ {PRODUCT_NAME} server started successfully")
            self.ostg_server_active = True
        else:
            self.log(
                f"{PRODUCT_NAME} server may not be active. "
                "Check: systemctl status netgen-server.service",
                "WARNING",
            )
            self.log("You can try: systemctl start netgen-server.service", "INFO")
            self.ostg_server_active = False
            # Capture recent journal logs so we can see why the server failed
            journal = self.run_command(
                "journalctl -u netgen-server -n 50 --no-pager 2>/dev/null",
                check=False,
                capture_output=True,
            )
            if journal.returncode == 0 and (journal.stdout or "").strip():
                self.log("Recent netgen-server logs (for debugging):", "INFO")
                for line in (journal.stdout or "").strip().split("\n")[-25:]:
                    self.log(f"  {line}")


    def verify_installation(self):
        """Verify the installation."""
        self.log("Verifying installation...")

        # Console-script entry points still ship under their original names
        # because the wheel itself was not renamed (surface rename only).
        commands_to_check = ["ostg-server", "ostg-client", "ostg-docker-install"]
        for cmd in commands_to_check:
            result = self.run_command(f"which {cmd}", check=False, capture_output=True)
            if result.returncode == 0:
                self.log(f"✓ {cmd} command available")
            else:
                self.log(f"✗ {cmd} command not found", "WARNING")

        # Check Docker image
        result = self.run_command(f"docker images {DOCKER_IMAGE}", check=False, capture_output=True)
        if result.stdout and DOCKER_IMAGE in result.stdout:
            self.log(f"✓ Docker image {DOCKER_IMAGE} available")
            self.docker_frr_available = True
        else:
            self.log(f"✗ Docker image {DOCKER_IMAGE} not found", "WARNING")
            self.docker_frr_available = False

        # Check systemd services
        services_to_check = [
            "netgen-server.service",
            "netgen-client.service",
            "netgen-cleanup.service",
            "netgen-cleanup.timer",
        ]
        for service in services_to_check:
            result = self.run_command(f"systemctl is-enabled {service}", check=False, capture_output=True)
            if result.stdout.strip() == "enabled":
                self.log(f"✓ {service} enabled")
            else:
                self.log(f"✗ {service} not enabled", "WARNING")

        self.log("✓ Installation verification completed")
        
    def test_frr_functionality(self):
        """Test FRR functionality (skip if FRR image was not built)"""
        self.log("Testing FRR functionality...")
        
        if not getattr(self, "docker_frr_available", False):
            self.log("Skipping FRR test (Docker image not available)", "INFO")
            return

        # Test Docker container creation
        test_container_name = "netgen-frr-test-install"
        
        try:
            # Create test container
            run_result = self.run_command(f"docker run -d --name {test_container_name} --network host {DOCKER_IMAGE}", check=False)
            if run_result.returncode != 0:
                self.log("Could not start FRR test container (image missing or run failed)", "WARNING")
                return
            
            # Wait for container to start
            self.run_command("sleep 10")
            
            # Test FRR functionality
            result = self.run_command(f"docker exec {test_container_name} vtysh -c 'show version'", check=False, capture_output=True)
            if result.returncode == 0:
                self.log("✓ FRR daemons are running")
            else:
                self.log("FRR daemons may not be running properly", "WARNING")
                
            # Check all FRR daemons (mgmtd, zebra, bgpd, ospfd, isisd)
            # Use pgrep inside the container to avoid pipe quoting issues
            mgmtd_check = self.run_command(f"docker exec {test_container_name} pgrep -c mgmtd", check=False, capture_output=True)
            zebra_check = self.run_command(f"docker exec {test_container_name} pgrep -c zebra", check=False, capture_output=True)
            bgpd_check = self.run_command(f"docker exec {test_container_name} pgrep -c bgpd", check=False, capture_output=True)
            ospfd_check = self.run_command(f"docker exec {test_container_name} pgrep -c ospfd", check=False, capture_output=True)
            isisd_check = self.run_command(f"docker exec {test_container_name} pgrep -c isisd", check=False, capture_output=True)
            
            mgmtd_count = int(mgmtd_check.stdout.strip()) if mgmtd_check.returncode == 0 else 0
            zebra_count = int(zebra_check.stdout.strip()) if zebra_check.returncode == 0 else 0
            bgpd_count = int(bgpd_check.stdout.strip()) if bgpd_check.returncode == 0 else 0
            ospfd_count = int(ospfd_check.stdout.strip()) if ospfd_check.returncode == 0 else 0
            isisd_count = int(isisd_check.stdout.strip()) if isisd_check.returncode == 0 else 0
            daemons_running = mgmtd_count + zebra_count + bgpd_count + ospfd_count + isisd_count
            
            if daemons_running > 0:
                daemon_status = []
                if mgmtd_count > 0:
                    daemon_status.append(f"mgmtd: {mgmtd_count}")
                if zebra_count > 0:
                    daemon_status.append(f"zebra: {zebra_count}")
                if bgpd_count > 0:
                    daemon_status.append(f"bgpd: {bgpd_count}")
                if ospfd_count > 0:
                    daemon_status.append(f"ospfd: {ospfd_count}")
                if isisd_count > 0:
                    daemon_status.append(f"isisd: {isisd_count}")
                self.log(f"✓ {daemons_running} FRR daemons running ({', '.join(daemon_status)})")
            else:
                self.log("Could not verify FRR daemons are running", "WARNING")
            
            # Test host networking connectivity
            result = self.run_command(f"docker exec {test_container_name} ping -c 1 8.8.8.8", check=False, capture_output=True)
            if result.returncode == 0:
                self.log("✓ Host networking is working (can reach external IPs)")
            else:
                self.log("Host networking may not be working properly", "WARNING")
                
        finally:
            # Cleanup test container
            self.run_command(f"docker stop {test_container_name}", check=False)
            self.run_command(f"docker rm {test_container_name}", check=False)
            self.log("✓ Test container cleaned up")
            
    def cleanup_old_install(self):
        """Remove artifacts from a prior OSTG install before laying down Netgen."""
        self.log("Checking for prior OSTG install to clean up...")

        # Stop and disable legacy systemd units (ignore errors — units may not exist)
        for unit in LEGACY_SYSTEMD_UNITS:
            self.run_command(f"systemctl stop {unit}", check=False)
            self.run_command(f"systemctl disable {unit}", check=False)
            self.run_command(f"rm -f /etc/systemd/system/{unit}", check=False)
        self.run_command("systemctl daemon-reload", check=False)

        # Kill any straggling server process
        self.run_command("pkill -f run_tgen_server.py", check=False)

        # Remove legacy install directory
        self.run_command(f"rm -rf {LEGACY_INSTALL_DIR}", check=False)

        # Remove legacy docker image and network (best-effort)
        self.run_command(f"docker rmi {LEGACY_DOCKER_IMAGE}", check=False)
        self.run_command(f"docker network rm {LEGACY_DOCKER_NETWORK}", check=False)

        self.log("✓ Legacy OSTG artifacts cleaned up (if any were present)")

    def install_remote(self):
        """Install Netgen on a remote host."""
        self.log(f"Installing {PRODUCT_NAME} on remote host: {self.remote_host}")

        # Check if sshpass is available
        if not shutil.which("sshpass"):
            self.log("sshpass is required for remote installation. Please install it first.", "ERROR")
            sys.exit(1)

        # Test SSH connection — surface ssh's actual error if it fails
        result = self.run_command("echo 'SSH connection test'", check=False, capture_output=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            self.log(f"Failed to connect to {self.remote_host} (exit {result.returncode})", "ERROR")
            if stderr:
                self.log(f"ssh stderr: {stderr}", "ERROR")
            sys.exit(1)

        self.log("✓ SSH connection successful")

        # Run installation steps
        self.cleanup_old_install()
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        self.install_ai_dependencies()
        self.install_ollama()
        self.setup_docker_frr()
        self.create_systemd_services()
        self.start_ostg_services()
        self.verify_installation()
        self.test_frr_functionality()

    def install_local(self):
        """Install Netgen locally."""
        self.log(f"Installing {PRODUCT_NAME} locally...")

        # Check if running as root
        if os.geteuid() != 0:
            self.log("This script must be run as root for local installation", "ERROR")
            sys.exit(1)

        # Run installation steps
        self.cleanup_old_install()
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        self.install_ai_dependencies()
        self.install_ollama()
        self.setup_docker_frr()
        self.create_systemd_services()
        self.start_ostg_services()
        self.verify_installation()
        self.test_frr_functionality()
        
    def run(self):
        """Main installation function."""
        banner = f"{PRODUCT_NAME} {NETGEN_VERSION} Complete Installation Script (Python Version)"
        self.log("=" * 60)
        self.log(banner)
        self.log("=" * 60)
        self.log(f"System: {self.system_info['os']} {self.system_info['distro']}")
        self.log(f"Package Manager: {self.system_info['package_manager']}")
        self.log(f"Python Command: {self.system_info['python_cmd']}")

        if self.remote_install:
            self.install_remote()
        else:
            self.install_local()

        self.log("=" * 60)
        self.log(f"{PRODUCT_NAME} installation completed successfully!")
        self.log("=" * 60)
        self.log("")
        # Re-check server status so "Next steps" reflects current state (e.g. if server crashed after start)
        result = self.run_command(
            "systemctl is-active netgen-server.service", check=False, capture_output=True
        )
        self.ostg_server_active = result.returncode == 0 and (result.stdout or "").strip() == "active"
        self.log("Next steps:")
        if self.ostg_server_active:
            self.log(f"1. ✓ {PRODUCT_NAME} server is already running")
        else:
            self.log(f"1. Start {PRODUCT_NAME} server: systemctl start netgen-server.service")
            self.log("   If it fails: journalctl -u netgen-server -n 50 --no-pager")
        self.log("2. ✓ Systemd services are configured")
        if self.docker_frr_available:
            self.log("3. ✓ Docker FRR image is ready")
        else:
            self.log("3. Docker FRR image not built; BGP/OSPF/ISIS devices unavailable until built.")
        self.log("4. You can now create devices and configure BGP")
        self.log("")
        self.log("To monitor logs:")
        self.log("  journalctl -u netgen-server -f")
        self.log("")
        self.log("To check status:")
        self.log("  systemctl status netgen-server.service")
        if getattr(self, "install_log_path", None):
            for h in self.logger.handlers:
                h.flush()
            self.log("")
            self.log("Full install log saved to:")
            self.log(f"  {self.install_log_path}")
            if self.remote_install:
                try:
                    remote_log = f"{INSTALL_DIR}/install.log"
                    subprocess.run(
                        ["sshpass", "-p", self.remote_pass, "scp", self.install_log_path,
                         f"{self.remote_user}@{self.remote_host}:{remote_log}"],
                        check=False, capture_output=True, timeout=30
                    )
                    self.log(f"  (copy on server: {self.remote_host}:{remote_log})")
                except Exception:
                    pass
        self.log("=" * 60)


def main():
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} Complete Installation Script")
    parser.add_argument("-H", "--host", help="Remote host for installation")
    parser.add_argument("-u", "--user", default="root", help="Remote user (default: root)")
    parser.add_argument("-p", "--password", help="Remote password")

    args = parser.parse_args()

    if args.host and not args.password:
        print("Error: Password is required for remote installation. Use -p or --password option.")
        sys.exit(1)

    installer = NetgenInstaller(
        remote_host=args.host,
        remote_user=args.user,
        remote_pass=args.password,
    )

    installer.run()


if __name__ == "__main__":
    main()
