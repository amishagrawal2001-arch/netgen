#!/usr/bin/env python3
"""
OSTG Complete Installation Script (Python Version)
Installs OSTG Traffic Generator with all dependencies including Docker and FRR
"""

import os
import sys
import subprocess
import platform
import shutil
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Configuration
OSTG_VERSION = "0.1.52"
PYTHON_VERSION = "3.10"
VENV_NAME = "ostg_env"
OSTG_PORT = 5051
DOCKER_IMAGE = "ostg-frr:latest"
DOCKER_NETWORK = "ostg-frr-network"
INSTALL_DIR = "/opt/OSTG"

# Color codes for output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

class OSTGInstaller:
    def __init__(self, remote_host: Optional[str] = None, remote_user: str = "root", remote_pass: Optional[str] = None):
        self.remote_host = remote_host
        self.remote_user = remote_user
        self.remote_pass = remote_pass
        self.remote_install = remote_host is not None
        self.setup_logging()
        self.system_info = self._detect_system()
        
    def setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('/tmp/ostg_install_temp.log')
            ]
        )
        self.logger = logging.getLogger(__name__)
        
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
        
    def run_command(self, command: str, check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
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
            return subprocess.run(ssh_cmd, shell=True, check=check, capture_output=capture_output, text=True)
        else:
            # Run command locally
            return subprocess.run(command, shell=True, check=check, capture_output=capture_output, text=True, env=env)
            
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
            
            try:
                self.run_command(f"apt-get update")
                self.run_command(f"apt-get install -y {' '.join(packages_to_install)}")
            except subprocess.CalledProcessError as e:
                self.log(f"Package installation failed: {e}", "ERROR")
                # Try to fix broken packages
                self.run_command("apt-get --fix-broken install -y", check=False)
                # Try installation again
                self.run_command(f"apt-get install -y {' '.join(packages_to_install)}")
                
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
            self.run_command("apt-get update")
            self.run_command("apt-get install -y software-properties-common")
            self.run_command("add-apt-repository -y ppa:deadsnakes/ppa")
            self.run_command("apt-get update")
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
            self.run_command("apt-get update")
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
        """Install OSTG Traffic Generator"""
        self.log("Installing OSTG Traffic Generator...")
        
        # Create installation directory
        self.run_command(f"mkdir -p {INSTALL_DIR}")
        
        # Copy wheel file
        wheel_file = f"ostg_trafficgen-{OSTG_VERSION}-py3-none-any.whl"
        local_wheel_path = f"dist/{wheel_file}"
        remote_wheel_path = f"{INSTALL_DIR}/{wheel_file}"
        
        if not os.path.exists(local_wheel_path):
            self.log(f"Wheel file not found: {local_wheel_path}", "ERROR")
            sys.exit(1)
            
        self.copy_file(local_wheel_path, remote_wheel_path)
        
        # Install wheel
        self.run_command(f"pip3 install {remote_wheel_path}")
        
        # Copy additional files
        files_to_copy = [
            ("Dockerfile.frr", f"{INSTALL_DIR}/Dockerfile.frr"),
            ("frr.conf.template", f"{INSTALL_DIR}/frr.conf.template"),
            ("start-frr.sh", f"{INSTALL_DIR}/start-frr.sh")
        ]
        
        for local_file, remote_file in files_to_copy:
            if os.path.exists(local_file):
                self.copy_file(local_file, remote_file)
                self.run_command(f"chmod +x {remote_file}")
                
        self.log("✓ OSTG installed successfully")
        
    def setup_docker_frr(self):
        """Setup Docker FRR image"""
        self.log("Setting up Docker FRR image...")
        
        # Build FRR Docker image
        dockerfile_path = f"{INSTALL_DIR}/Dockerfile.frr"
        self.run_command(f"docker build -t {DOCKER_IMAGE} -f {dockerfile_path} {INSTALL_DIR}")
        
        # Create Docker network
        result = self.run_command(f"docker network create {DOCKER_NETWORK}", check=False)
        if result.returncode != 0:
            self.log(f"Docker network {DOCKER_NETWORK} may already exist", "WARNING")
            
        self.log("✓ Docker FRR setup completed successfully")
        
    def create_systemd_services(self):
        """Create systemd services"""
        self.log("Creating systemd services...")
        
        # OSTG Server Service
        server_service = f"""[Unit]
Description=OSTG Traffic Generator Server
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 /usr/local/lib/python3.10/dist-packages/run_tgen_server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ostg-server

[Install]
WantedBy=multi-user.target
"""
        
        self.run_command(f"cat > /etc/systemd/system/ostg-server.service << 'EOF'\n{server_service}EOF")
        
        # OSTG Client Service
        client_service = f"""[Unit]
Description=OSTG Traffic Generator Client
After=network.target ostg-server.service
Requires=ostg-server.service

[Service]
Type=simple
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 /usr/local/lib/python3.10/dist-packages/run_tgen_client.py

[Install]
WantedBy=multi-user.target
"""
        
        self.run_command(f"cat > /etc/systemd/system/ostg-client.service << 'EOF'\n{client_service}EOF")
        
        # Cleanup Service
        cleanup_service = f"""[Unit]
Description=OSTG Cleanup Service
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory={INSTALL_DIR}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 -c "from utils.frr_docker import cleanup_all_containers; cleanup_all_containers()"
"""
        
        self.run_command(f"cat > /etc/systemd/system/ostg-cleanup.service << 'EOF'\n{cleanup_service}EOF")
        
        # Cleanup Timer
        cleanup_timer = """[Unit]
Description=Run OSTG Cleanup Service every 5 minutes
Requires=ostg-cleanup.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
"""
        
        self.run_command(f"cat > /etc/systemd/system/ostg-cleanup.timer << 'EOF'\n{cleanup_timer}EOF")
        
        # Reload systemd and enable services
        self.run_command("systemctl daemon-reload")
        self.run_command("systemctl enable ostg-server.service")
        self.run_command("systemctl enable ostg-cleanup.timer")
        
        self.log("✓ Systemd services created successfully")
        
    def start_ostg_services(self):
        """Start OSTG services"""
        self.log("Starting OSTG services...")
        
        # Stop any existing processes
        self.run_command("pkill -f run_tgen_server.py", check=False)
        
        # Start OSTG server
        self.run_command("systemctl start ostg-server.service")
        
        # Check status
        result = self.run_command("systemctl is-active ostg-server.service", capture_output=True)
        if result.stdout.strip() == "active":
            self.log("✓ OSTG server started successfully")
        else:
            self.log("Failed to start OSTG server", "ERROR")
            sys.exit(1)
            
    def verify_installation(self):
        """Verify the installation"""
        self.log("Verifying installation...")
        
        # Check OSTG commands
        commands_to_check = ["ostg-server", "ostg-client", "ostg-docker-install"]
        for cmd in commands_to_check:
            result = self.run_command(f"which {cmd}", check=False, capture_output=True)
            if result.returncode == 0:
                self.log(f"✓ {cmd} command available")
            else:
                self.log(f"✗ {cmd} command not found", "WARNING")
                
        # Check Docker image
        result = self.run_command(f"docker images {DOCKER_IMAGE}", check=False, capture_output=True)
        if DOCKER_IMAGE in result.stdout:
            self.log(f"✓ Docker image {DOCKER_IMAGE} available")
        else:
            self.log(f"✗ Docker image {DOCKER_IMAGE} not found", "WARNING")
            
        # Check systemd services
        services_to_check = ["ostg-server.service", "ostg-client.service", "ostg-cleanup.service", "ostg-cleanup.timer"]
        for service in services_to_check:
            result = self.run_command(f"systemctl is-enabled {service}", check=False, capture_output=True)
            if result.stdout.strip() == "enabled":
                self.log(f"✓ {service} enabled")
            else:
                self.log(f"✗ {service} not enabled", "WARNING")
                
        self.log("✓ Installation verification completed")
        
    def test_frr_functionality(self):
        """Test FRR functionality"""
        self.log("Testing FRR functionality...")
        
        # Test Docker container creation
        test_container_name = "ostg-frr-test-install"
        
        try:
            # Create test container
            self.run_command(f"docker run -d --name {test_container_name} --network host {DOCKER_IMAGE}")
            
            # Wait for container to start
            self.run_command("sleep 10")
            
            # Test FRR functionality
            result = self.run_command(f"docker exec {test_container_name} vtysh -c 'show version'", check=False, capture_output=True)
            if result.returncode == 0:
                self.log("✓ FRR daemons are running")
            else:
                self.log("FRR daemons may not be running properly", "WARNING")
                
            # Check BGP and Zebra daemons
            result = self.run_command(f"docker exec {test_container_name} ps aux | grep -E '(zebra|bgpd)' | wc -l", capture_output=True)
            daemons_running = int(result.stdout.strip())
            self.log(f"✓ {daemons_running} FRR daemons running (BGP and Zebra only)")
            
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
            
    def install_remote(self):
        """Install OSTG on a remote host"""
        self.log(f"Installing OSTG on remote host: {self.remote_host}")
        
        # Check if sshpass is available
        if not shutil.which("sshpass"):
            self.log("sshpass is required for remote installation. Please install it first.", "ERROR")
            sys.exit(1)
            
        # Test SSH connection
        result = self.run_command("echo 'SSH connection test'", check=False, capture_output=True)
        if result.returncode != 0:
            self.log(f"Failed to connect to {self.remote_host}", "ERROR")
            sys.exit(1)
            
        self.log("✓ SSH connection successful")
        
        # Run installation steps
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        self.setup_docker_frr()
        self.create_systemd_services()
        self.start_ostg_services()
        self.verify_installation()
        self.test_frr_functionality()
        
    def install_local(self):
        """Install OSTG locally"""
        self.log("Installing OSTG locally...")
        
        # Check if running as root
        if os.geteuid() != 0:
            self.log("This script must be run as root for local installation", "ERROR")
            sys.exit(1)
            
        # Run installation steps
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        self.setup_docker_frr()
        self.create_systemd_services()
        self.start_ostg_services()
        self.verify_installation()
        self.test_frr_functionality()
        
    def run(self):
        """Main installation function"""
        self.log("=" * 60)
        self.log("OSTG Complete Installation Script (Python Version)")
        self.log("=" * 60)
        self.log(f"System: {self.system_info['os']} {self.system_info['distro']}")
        self.log(f"Package Manager: {self.system_info['package_manager']}")
        self.log(f"Python Command: {self.system_info['python_cmd']}")
        
        if self.remote_install:
            self.install_remote()
        else:
            self.install_local()
            
        self.log("=" * 60)
        self.log("OSTG Installation Completed Successfully!")
        self.log("=" * 60)
        self.log("")
        self.log("Next steps:")
        self.log("1. ✓ OSTG server is already running")
        self.log("2. ✓ Systemd services are configured")
        self.log("3. ✓ Docker FRR image is ready")
        self.log("4. You can now create devices and configure BGP")
        self.log("")
        self.log("To monitor logs:")
        self.log("  journalctl -u ostg-server -f")
        self.log("")
        self.log("To check status:")
        self.log("  systemctl status ostg-server.service")
        self.log("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="OSTG Complete Installation Script")
    parser.add_argument("-H", "--host", help="Remote host for installation")
    parser.add_argument("-u", "--user", default="root", help="Remote user (default: root)")
    parser.add_argument("-p", "--password", help="Remote password")
    
    args = parser.parse_args()
    
    if args.host and not args.password:
        print("Error: Password is required for remote installation. Use -p or --password option.")
        sys.exit(1)
        
    installer = OSTGInstaller(
        remote_host=args.host,
        remote_user=args.user,
        remote_pass=args.password
    )
    
    installer.run()


if __name__ == "__main__":
    main()
