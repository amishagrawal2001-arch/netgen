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
import shlex
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Product identity
PRODUCT_NAME = "Netgen"

# Wheel artifact still ships under its original distribution name; surface rename
# leaves the Python package internals untouched.
WHEEL_DIST = "ostg_trafficgen"


def _parse_pyproject_version() -> str:
    """Read `version = "..."` from pyproject.toml.

    Hardcoding the version here was the source of every "wheel built
    as 0.2.4 but installer looks for 0.2.0" bug. Parsing the canonical
    source removes the need to keep this file in lock-step with
    pyproject.toml.
    """
    import os as _os
    import re as _re
    pyproject_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "pyproject.toml",
    )
    try:
        with open(pyproject_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    # Last-ditch fallback so the installer doesn't crash if
    # pyproject.toml is missing (shouldn't happen with the repo, but
    # we can be defensive). Anything that builds the wheel will still
    # discover the actual version downstream.
    return "0.0.0"


NETGEN_VERSION = _parse_pyproject_version()
WHEEL_VERSION = NETGEN_VERSION

PYTHON_VERSION = "3.10"
VENV_NAME = "netgen_env"
NETGEN_PORT = 5051
DOCKER_IMAGE = "netgen-frr:latest"
# DOCKER_NETWORK constant removed: FRR containers run on host networking
# (`network_mode='host'`), so no docker bridge is created by the runtime.
INSTALL_DIR = "/opt/netgen"

# Legacy names retained only for clean-up of prior OSTG installs.
# LEGACY_DOCKER_NETWORK is the bridge older installs created — we still
# remove it on upgrade so it doesn't linger as a dead interface.
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
    def __init__(
        self,
        remote_host: Optional[str] = None,
        remote_user: str = "root",
        remote_pass: Optional[str] = None,
        install_dpdk: bool = True,
        skip_dpdk_build: bool = False,
        prebuilt_wheel: Optional[str] = None,
    ):
        self.remote_host = remote_host
        self.remote_user = remote_user
        self.remote_pass = remote_pass
        self.remote_install = remote_host is not None
        self.ostg_server_active = False
        self.docker_frr_available = False
        # DPDK runtime install (libraries + tx_worker binary). Default ON;
        # disable with --no-dpdk for hosts that won't generate traffic
        # (e.g. devbox-only installs). install_dpdk.sh respects SKIP_BUILD=1
        # to deploy only the apt prereqs without compiling DPDK + tx_worker —
        # useful when DPDK is already installed system-wide.
        self.install_dpdk = install_dpdk
        self.skip_dpdk_build = skip_dpdk_build
        # Pre-built wheel path (--wheel). When set, _build_wheel skips
        # the `python -m build` step and uses this file. Required for
        # the in-GUI install dialog flow where there's no source tree
        # on the target box — only the wheel and the installer script
        # got sftp'd over.
        self._prebuilt_wheel = prebuilt_wheel
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

    # ─────────────────────────────────────── apt helpers (v0.3.16+)
    #
    # Standard non-interactive incantation for apt-get install. Always
    # use this wrapper, never bare `apt-get install -y`, because the
    # `-y` flag does NOT cover all interactive prompts dpkg can throw.
    # Specifically, dpkg's CONFFILE prompt (when a package's default
    # config differs from a file on the system that was created by
    # you or a previous install) is NOT controlled by
    # DEBIAN_FRONTEND/UCF_FORCE_CONFFNEW — only by the
    # `--force-confdef` / `--force-confold` dpkg flags passed via
    # apt's `Dpkg::Options::=` mechanism.
    #
    # Triggering trace this method was born from (v0.3.16 fresh
    # install on Ubuntu 24.04 Noble):
    #
    #   Setting up containerd.io ...
    #   Configuration file '/etc/containerd/config.toml'
    #     ==> File on system created by you or by a script.
    #     ==> File also in package provided by package maintainer.
    #       What would you like to do about it?
    #   *** config.toml (Y/I/N/O/D/Z) [default=N] ?
    #   dpkg: error processing package containerd.io (--configure):
    #     end of file on stdin at conffile prompt
    #   E: Sub-process /usr/bin/dpkg returned an error code (1)
    #
    # The `nohup`-detached install has no tty, so dpkg saw EOF on
    # stdin and bailed. The fix is the `Dpkg::Options::=--force-*`
    # flags below, which tell dpkg to auto-resolve conffile conflicts
    # without prompting:
    #   --force-confdef: if the diff is in the maintainer's defaults
    #                    only (no local edits), take the new version
    #   --force-confold: if there ARE local edits, preserve them
    # Combined: a safe non-interactive default that respects operator
    # customizations.
    _APT_DPKG_FLAGS = (
        "-o Dpkg::Options::=--force-confdef "
        "-o Dpkg::Options::=--force-confold"
    )

    def _install_apt_keyring(self, name: str, key_url: str,
                             *, check: bool = True) -> None:
        """Download an apt repository signing key + dearmor it into
        ``/etc/apt/keyrings/<name>.gpg``.

        v0.3.16+: replaces the historical
        ``curl -fsSL <url> | gpg --dearmor -o ...`` one-liner, which
        fails in a nohup-detached install with:

            gpg: cannot open '/dev/tty': No such device or address
            subprocess.CalledProcessError: ... returned non-zero exit
            status 2.

        Root cause: modern GnuPG 2.x defaults to interactive mode and
        touches ``/dev/tty`` for pinentry even on operations like
        ``--dearmor`` that don't need a passphrase. With no
        controlling terminal (nohup detached), the open fails and
        gpg exits 2.

        Secondary problem: the original pipe ``curl | gpg`` returned
        gpg's exit code, not curl's. A failed curl (network blip,
        404 on the key URL) would still produce an empty .gpg file
        and the next ``apt-get update`` would silently fail with
        ``NO_PUBKEY`` instead of pointing at the real cause.

        Fix:
          1. Download key to a tmp file (curl failure surfaces here)
          2. Dearmor with --batch --no-tty (never touches /dev/tty)
          3. chmod the keyring to 0644 (apt's expected permissions)
          4. Clean up the tmp file
        """
        keyring = f"/etc/apt/keyrings/{name}.gpg"
        tmp = f"/tmp/.netgen-apt-key-{name}.asc"
        self.run_command("mkdir -p /etc/apt/keyrings", check=check)
        # Step 1: download. curl -f causes non-zero exit on HTTP
        # errors; without it a 404 would silently produce an HTML
        # error-page file that gpg would happily "dearmor" into
        # garbage.
        self.run_command(
            f"curl -fsSL {key_url} -o {tmp}", check=check,
        )
        # Step 2: dearmor with the flags that suppress the
        # /dev/tty + pinentry interaction.
        #   --batch + --no-tty: never touch the terminal
        #   --yes: don't prompt on existing output file
        # Remove any stale keyring first so --yes never has to
        # decide (some older gpg builds prompt on overwrite even
        # with --yes if --batch is missing).
        self.run_command(f"rm -f {keyring}", check=False)
        self.run_command(
            f"gpg --batch --no-tty --yes --dearmor -o {keyring} {tmp}",
            check=check,
        )
        # Step 3: apt's expected perms — group-readable so
        # _apt non-root user (when present) can read.
        self.run_command(f"chmod 0644 {keyring}", check=False)
        # Step 4: cleanup. Not catastrophic if it fails.
        self.run_command(f"rm -f {tmp}", check=False)

    def _apt_install(self, packages: str, *, check: bool = True,
                     extra_opts: str = ""):
        """``apt-get install -y`` with the full non-interactive set.

        Always use this instead of bare ``apt-get install -y`` so
        the dpkg conffile prompt can never EOF-fail a non-interactive
        install.

        Args:
          packages: space-separated package list (e.g. "docker-ce
                    containerd.io"). Pass exactly the same string
                    you'd pass to ``apt-get install``.
          check:    same semantics as ``run_command`` — raise on rc≠0
                    when True.
          extra_opts: appended verbatim before the package list, for
                    callers that need their own --option flags (e.g.
                    Acquire timeouts).

        ``run_command`` already exports DEBIAN_FRONTEND=noninteractive
        in env, so we don't need to prepend it here. The dpkg flags
        do need to be on the apt command line.
        """
        cmd = (
            f"apt-get install -y {self._APT_DPKG_FLAGS} "
            f"{extra_opts} {packages}"
        ).strip()
        # Collapse any double-spaces from empty extra_opts.
        cmd = " ".join(cmd.split())
        return self.run_command(cmd, check=check)
            
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

        # v0.3.12: RDMA userspace + perftest. Independent of DPDK —
        # even --no-dpdk hosts need these for the Tools → RDMA workflows
        # (Blast a RDMA Flow + per-stream engine=rdma path). On DPDK
        # hosts, install_dpdk.sh's apt install duplicates these names;
        # apt-get install on already-present packages is a fast no-op
        # so duplication is intentional belt-and-suspenders.
        #
        # Best-effort: wrapped in its own try/except so a missing
        # perftest package on an exotic distro doesn't fail the whole
        # install. On RHEL 7/8 perftest comes from EPEL, which
        # _install_yum_packages already enables; on RHEL 9+/Fedora
        # it's in the main repo; on stock Debian/Ubuntu/Alpine/openSUSE
        # it's also in the main repo. Distros where it's genuinely
        # absent: the operator falls back to Install Guide §10's
        # manual one-liner.
        try:
            self._install_rdma_userspace()
        except Exception as e:
            self.log(f"RDMA userspace install skipped: {e}", "WARNING")
            self.log("Tools → RDMA features will be disabled until "
                     "perftest is installed manually — see Install Guide §10.",
                     "WARNING")

    def _install_rdma_userspace(self):
        """Install perftest + verbs userspace for v0.3.12 RDMA traffic-gen.

        Distro-specific package names:
          apt  → perftest rdma-core libibverbs-dev libmlx5-dev
          dnf  → perftest rdma-core libibverbs-devel libmlx5-devel
          yum  → same as dnf (RHEL 7/8; perftest from EPEL, enabled
                              earlier in _install_yum_packages)
          apk  → perftest rdma-core libibverbs-dev
                 (Alpine bundles libmlx5 into rdma-core; no separate package)
          zypper → perftest rdma-core libibverbs-devel libmlx5-devel

        check=False on every command so a partial set (e.g. perftest
        present but libmlx5 absent on a niche distro) still installs
        what it can. ~3 MB total when everything lands; rdma-core is
        typically already present on modern distros.
        """
        pm = self.system_info["package_manager"]
        self.log("Installing RDMA userspace + perftest (for Tools → RDMA)...")
        if pm == "apt":
            self._wait_for_apt_lock()
            self._apt_install(
                "perftest rdma-core libibverbs-dev libmlx5-dev",
                check=False,
            )
        elif pm in ("dnf", "yum"):
            self.run_command(
                f"{pm} install -y perftest rdma-core "
                "libibverbs-devel libmlx5-devel",
                check=False,
            )
        elif pm == "apk":
            self.run_command(
                "apk add perftest rdma-core libibverbs-dev",
                check=False,
            )
        elif pm == "zypper":
            self.run_command(
                "zypper install -y perftest rdma-core "
                "libibverbs-devel libmlx5-devel",
                check=False,
            )
        else:
            self.log(
                f"No RDMA install path defined for package manager: {pm}. "
                "Install perftest manually — see Install Guide §10.",
                "WARNING",
            )
            return

        # Verify what landed. perftest is the canonical signal — if
        # ib_send_bw is on PATH, the rest is irrelevant for the GUI.
        check = self.run_command("which ib_send_bw", check=False,
                                 capture_output=True)
        if check.returncode == 0:
            self.log("RDMA userspace + perftest installed (Tools → RDMA "
                     "is ready to use)", "INFO")
        else:
            self.log("perftest binary not on PATH after install — "
                     "Tools → RDMA will report 'perftest not installed'. "
                     "See Install Guide §10 for the manual install path.",
                     "WARNING")

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
                self._apt_install(" ".join(packages_to_install))
            except subprocess.CalledProcessError as e:
                self.log(f"Package installation encountered issues: {e}", "WARNING")
                # Try to fix broken packages
                fix_result = self.run_command("apt-get --fix-broken install -y", check=False)
                if fix_result.returncode != 0:
                    self.log("Some packages may have dependency issues (e.g., NVIDIA drivers)", "WARNING")
                    self.log("This is usually non-critical. Continuing with installation...", "WARNING")
                # Try installation again, but don't fail if it still has issues
                retry_result = self._apt_install(" ".join(packages_to_install), check=False)
                if retry_result.returncode != 0:
                    self.log("Some system packages could not be installed due to dependency conflicts", "WARNING")
                    self.log("This may be due to NVIDIA driver conflicts. Continuing with OSTG installation...", "WARNING")
                
    def _wait_for_apt_lock(self):
        """Wait for any existing apt/dpkg processes to release the lock.

        Uses fuser on the actual lock files instead of pgrep because:
          • `pgrep -f '(apt|dpkg)'` matched its own `sh -c 'pgrep ...'`
            wrapper (the cmdline contains the literal characters "apt"
            and "dpkg"), so the check always returned positive — every
            call thought an apt process was running and entered the
            10s sleep loop. Operators saw "Waiting for apt processes
            to finish... (0s, 10s, 20s, ...)" indefinitely until the
            5-min timeout, then proceeded — and gave up before that.
          • `fuser <lockfile>` is what apt itself uses internally; it
            only returns success if the kernel says some process has
            the lock file open via an fd. The fuser binary's own
            cmdline doesn't open the lock file, so no self-match.
          • Also: this is what /usr/bin/wait-for-apt does on Debian.

        Returns immediately when no lock is held. The 5-min timeout
        is a safety net for the genuine case (apt-daily.service /
        unattended-upgrades mid-update). Falls through with a
        WARNING and proceeds if the timeout fires — pip install
        wheel doesn't actually need the apt lock.
        """
        import time
        max_wait = 300  # 5 minutes
        wait_time = 0
        # The four canonical apt/dpkg lock files. fuser is OK with
        # paths that don't exist (silently skips them).
        locks = (
            "/var/lib/dpkg/lock /var/lib/dpkg/lock-frontend "
            "/var/lib/apt/lists/lock /var/cache/apt/archives/lock"
        )

        while wait_time < max_wait:
            # fuser exits 0 if at least one of the listed files is
            # held by some process, 1 if none are held. stderr noise
            # ("Specified filename ... does not exist") suppressed.
            result = self.run_command(
                f"fuser {locks} 2>/dev/null",
                check=False, capture_output=True,
            )
            holder_pids = (result.stdout or "").strip()
            if result.returncode != 0 or not holder_pids:
                self.log("✓ No apt/dpkg lock held")
                return

            # Identify the holder so operators see what's blocking
            # — much better diagnostic than "waiting" with no detail.
            who = self.run_command(
                f"ps -o pid,etime,cmd -p {holder_pids} --no-headers 2>/dev/null | head -3",
                check=False, capture_output=True,
            )
            who_lines = (who.stdout or "").strip().replace("\n", " | ")
            self.log(
                f"Waiting for apt/dpkg lock to release... ({wait_time}s) — "
                f"holder: {who_lines or holder_pids}"
            )
            time.sleep(10)
            wait_time += 10

        self.log(
            "Timeout waiting for apt lock. Proceeding anyway — most "
            "install steps don't actually need the apt lock; if apt-get "
            "install fails later, run `apt --fix-broken install` and retry.",
            "WARNING",
        )
            
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
            self._apt_install("software-properties-common")
            self.run_command("add-apt-repository -y ppa:deadsnakes/ppa")
            update_result = self.run_command("apt-get update", check=False)
            if update_result.returncode != 0:
                self.log("apt-get update had some issues after adding PPA, but continuing...", "WARNING")
            self._apt_install("python3.10 python3.10-venv python3.10-dev python3.10-distutils")
            self._bootstrap_pip_for_python310()
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

    def _bootstrap_pip_for_python310(self):
        """Install pip for the freshly-installed python3.10 interpreter.

        Replaces the historical ``curl get-pip.py | python3.10`` one-liner,
        which fails on Ubuntu 24.04 (Noble) with:

            error: uninstall-no-record-file
            × Cannot uninstall packaging 24.0
            ╰─> The package's contents are unknown:
                no RECORD file was found for packaging.
            hint: The package was installed by debian.

        Root cause: get-pip.py downloads the latest pip + wheel + a
        bundled ``packaging`` and tries to UNINSTALL whatever it finds on
        sys.path first. On Noble the system ships ``python3-packaging``
        24.0 from apt — Debian-managed packages have no RECORD file
        (pip's safety check) so the uninstall step refuses, aborting
        the entire bootstrap. Reported in the field on the v0.3.16
        SFTP+heartbeat fresh-install path.

        Strategy (most-likely-to-work first):

        1. ``python3.10 -m ensurepip --upgrade --default-pip`` — stdlib
           bootstrapper bundled with ``python3.10-venv`` (already
           installed by the previous apt line). No network needed.
           Lands a working pip without touching the system packaging.

        2. ``curl get-pip.py | python3.10 - --ignore-installed`` — the
           old path with the ONE flag that suppresses the broken
           uninstall step. Pip installs its own ``packaging`` /
           ``wheel`` alongside the apt-managed copies; site-packages
           order resolves the conflict per interpreter (python3.10's
           site-packages takes precedence for ``python3.10``).

        Either branch ends with a sanity check (``python3.10 -m pip
        --version``) so callers see a clean ERROR if both bootstrap
        paths fail rather than a cryptic later import failure.
        """
        self.log("Bootstrapping pip for python3.10...")
        # Path 1: ensurepip. Fast, offline, no Debian-uninstall trap.
        r = self.run_command(
            "python3.10 -m ensurepip --upgrade --default-pip",
            check=False, capture_output=True,
        )
        if r.returncode == 0:
            self.log("  ✓ pip installed via ensurepip")
        else:
            self.log(
                f"  ensurepip failed (rc={r.returncode}); "
                f"falling back to get-pip.py --ignore-installed",
                "WARNING",
            )
            # Path 2: get-pip.py BUT with --ignore-installed so pip
            # doesn't try to uninstall the Debian-managed packaging.
            r2 = self.run_command(
                "curl -sS https://bootstrap.pypa.io/get-pip.py "
                "| python3.10 - --ignore-installed",
                check=False,
            )
            if r2.returncode != 0:
                self.log(
                    f"Both pip-bootstrap paths failed (ensurepip rc="
                    f"{r.returncode}, get-pip.py rc={r2.returncode}). "
                    "Install pip for python3.10 manually:\n"
                    "  sudo apt install -y python3.10-pip   # or\n"
                    "  sudo python3.10 -m ensurepip --upgrade",
                    "ERROR",
                )
                sys.exit(1)
            self.log("  ✓ pip installed via get-pip.py --ignore-installed")

        # Sanity check — pip is callable from python3.10.
        chk = self.run_command(
            "python3.10 -m pip --version",
            check=False, capture_output=True,
        )
        if chk.returncode == 0:
            self.log(f"  ✓ {chk.stdout.strip()}")
        else:
            self.log(
                f"pip bootstrapped but `python3.10 -m pip --version` "
                f"failed (rc={chk.returncode}). stderr={chk.stderr!r}",
                "ERROR",
            )
            sys.exit(1)

    def _fix_apt_gpg_keys(self):
        """Fix missing GPG keys for apt repositories"""
        self.log("Checking and fixing GPG keys for apt repositories...")
        
        # Check if InfluxData repository exists
        result = self.run_command("grep -r 'repos.influxdata.com' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null", check=False, capture_output=True)
        if result.returncode == 0 and "influxdata" in result.stdout.lower():
            self.log("InfluxData repository detected, adding GPG key...")
            # v0.3.16+: was an inline `curl | gpg --dearmor` pipe that
            # fails in nohup-detached installs with `gpg: cannot open
            # '/dev/tty'`. _install_apt_keyring adds --batch --no-tty
            # + curl-failure-surfacing.
            try:
                self._install_apt_keyring(
                    "influxdb",
                    "https://repos.influxdata.com/influxdb.key",
                    check=True,
                )
                self.log("✓ InfluxData GPG key added successfully")
            except subprocess.CalledProcessError:
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
            self._apt_install("ca-certificates curl gnupg lsb-release")
            # v0.3.16+: was an inline `curl | gpg --dearmor` pipe
            # that failed in nohup-detached installs with
            # `gpg: cannot open '/dev/tty': No such device or
            # address`. _install_apt_keyring adds the --batch
            # --no-tty flags and downloads-then-dearmors so curl
            # failures surface cleanly.
            self._install_apt_keyring(
                "docker",
                "https://download.docker.com/linux/ubuntu/gpg",
            )
            self.run_command('echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null')
            self.run_command("apt-get update")
            # THIS was the v0.3.16 fresh-install failure site —
            # containerd.io's config.toml conffile diff produced an
            # interactive prompt that EOF'd the non-tty install.
            # _apt_install adds --force-confdef + --force-confold to
            # auto-resolve.
            self._apt_install(
                "docker-ce docker-ce-cli containerd.io docker-compose-plugin"
            )
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
        
    def _build_wheel(self):
        """Build a fresh wheel from current source.

        Without this step, install_ostg copies whatever .whl is sitting
        in dist/ — which may be stale (built hours/days ago, missing
        in-repo fixes). The ARP-fails-but-ping-works mystery in the
        most recent session traced back exactly to this: a wheel built
        before the monitor 5051→5050 fix went out, deployed onto a
        fresh server, and the bug looked like it'd been fixed in
        source but the binary still had the old behavior. Always
        rebuild from current source so the deployed artifact and the
        git tree never drift.

        Exception: when self._prebuilt_wheel is set (operator passed
        --wheel <path>, typically from the in-GUI install dialog
        which sftp-copies a pre-built artifact alongside the
        installer), use that file as-is and skip the build step.
        The GUI dialog has no source tree on the target box, so
        `python -m build` would fail with "pyproject.toml not found"
        — exactly the rc=2 the operator just hit.
        """
        # Honor a pre-built wheel passed via --wheel / -w.
        #
        # Accept three forms:
        #   1. /path/to/ostg_trafficgen-0.2.7-py3-none-any.whl   (file)
        #   2. /path/to/dir/                                      (directory)
        #   3. /path/to/dir/ostg_trafficgen-*.whl                 (glob)
        #
        # The directory form is what early in-GUI install dialogs
        # (v0.2.6's widgets/install_server_dialog.py) sent — they
        # passed `-w /tmp/netgen_install`. The v0.2.7 dialog sends
        # the full file path. Tolerating both means an upgraded
        # installer rolls onto a target via an older client without
        # breaking — just glob the dir for *.whl, pick the newest.
        pw = getattr(self, "_prebuilt_wheel", None)
        if pw:
            import glob as _glob_pw
            if os.path.isdir(pw):
                candidates = sorted(
                    _glob_pw.glob(os.path.join(pw, f"{WHEEL_DIST}-*-py3-none-any.whl")),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if not candidates:
                    self.log(
                        f"--wheel directory {pw} contains no "
                        f"{WHEEL_DIST}-*-py3-none-any.whl files. "
                        f"Pass a file path (or sftp the wheel into "
                        f"the directory first).",
                        "ERROR",
                    )
                    sys.exit(1)
                pw = candidates[0]
                self.log(
                    f"--wheel pointed at a directory; auto-picked "
                    f"newest match: {pw}"
                )
            elif "*" in pw or "?" in pw:
                candidates = sorted(
                    _glob_pw.glob(pw),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if not candidates:
                    self.log(
                        f"--wheel glob {pw} matched no files",
                        "ERROR",
                    )
                    sys.exit(1)
                pw = candidates[0]
            if not os.path.isfile(pw):
                self.log(
                    f"--wheel path not found: {pw}",
                    "ERROR",
                )
                sys.exit(1)
            self._actual_wheel_path = os.path.abspath(pw)
            # install_ostg() reads BOTH _actual_wheel_path and
            # _actual_wheel_file (the bare basename, used for the
            # remote-side filename). Setting only _actual_wheel_path
            # made install_ostg fall through to its fallback that
            # computes `dist/ostg_trafficgen-{WHEEL_VERSION}-...whl`
            # — and WHEEL_VERSION on the target is 0.0.0 because
            # there's no pyproject.toml in the script dir. That
            # produced the misleading "Wheel file not found:
            # dist/ostg_trafficgen-0.0.0-py3-none-any.whl" error
            # operators hit when using the --wheel flag.
            self._actual_wheel_file = os.path.basename(self._actual_wheel_path)
            self.log(
                f"Using pre-built wheel: {self._actual_wheel_path} "
                f"(skipping `python -m build` — no source tree expected)",
            )
            return

        import subprocess as _sp
        import glob as _glob
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Compute the *expected* wheel filename (from pyproject.toml)
        # but don't hard-rely on it — after building, glob the dist/
        # directory and grab whatever's there. That way a bump
        # between this code reading the version and the build
        # actually finishing (e.g. someone edits pyproject mid-build)
        # doesn't cause a false-negative "not in dist/" error.
        wheel_file = f"{WHEEL_DIST}-{WHEEL_VERSION}-py3-none-any.whl"
        local_wheel_path = os.path.join(script_dir, "dist", wheel_file)

        self.log(f"Building fresh wheel from source (expecting {wheel_file})...")
        try:
            # Wipe any older artifacts in dist/ to avoid pip picking
            # up a stale one if the new build fails silently.
            dist_dir = os.path.join(script_dir, "dist")
            if os.path.isdir(dist_dir):
                for f in os.listdir(dist_dir):
                    if f.endswith((".whl", ".tar.gz")):
                        try:
                            os.remove(os.path.join(dist_dir, f))
                        except OSError:
                            pass

            # `python -m build` requires the `build` package; fall
            # back to setuptools' bdist_wheel if it's missing.
            result = _sp.run(
                [sys.executable, "-m", "build", "--wheel"],
                cwd=script_dir, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                # `build` not installed → use legacy bdist_wheel.
                self.log("python -m build failed; falling back to setup.py bdist_wheel", "WARNING")
                result = _sp.run(
                    [sys.executable, "setup.py", "bdist_wheel"],
                    cwd=script_dir, capture_output=True, text=True, timeout=300,
                )
            if result.returncode != 0:
                self.log(
                    "Wheel build failed:\n"
                    f"  stdout: {(result.stdout or '').strip()[-500:]}\n"
                    f"  stderr: {(result.stderr or '').strip()[-500:]}",
                    "ERROR",
                )
                sys.exit(1)

            # If the exact-name file isn't there, fall back to globbing
            # for any ostg_trafficgen-*.whl in dist/. Whoever ran
            # `python -m build` is the source of truth for what the
            # filename should be, not this script's static constants.
            if not os.path.exists(local_wheel_path):
                candidates = sorted(
                    _glob.glob(os.path.join(dist_dir, f"{WHEEL_DIST}-*-py3-none-any.whl")),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if not candidates:
                    self.log(
                        f"Wheel build reported success but no "
                        f"{WHEEL_DIST}-*.whl in dist/. "
                        f"Files present: {os.listdir(dist_dir) if os.path.isdir(dist_dir) else 'none'}",
                        "ERROR",
                    )
                    sys.exit(1)
                local_wheel_path = candidates[0]
                wheel_file = os.path.basename(local_wheel_path)
                self.log(
                    f"Build produced {wheel_file} (expected "
                    f"{WHEEL_DIST}-{WHEEL_VERSION}-py3-none-any.whl). "
                    f"Using {wheel_file}.",
                    "WARNING",
                )
            self._actual_wheel_path = local_wheel_path
            self._actual_wheel_file = wheel_file

            self.log(f"✓ Built {wheel_file}")
        except _sp.TimeoutExpired:
            self.log("Wheel build timed out after 5 minutes", "ERROR")
            sys.exit(1)
        except FileNotFoundError:
            self.log(
                "Python not in PATH for wheel build — install the "
                "`build` package: pip install build",
                "ERROR",
            )
            sys.exit(1)

    def install_ostg(self):
        """Install the Netgen traffic generator wheel and ancillary files."""
        self.log(f"Installing {PRODUCT_NAME} traffic generator...")

        # Create installation directory
        self.run_command(f"mkdir -p {INSTALL_DIR}")

        # Rebuild the wheel from current source before deploying so
        # the deployed artifact can't drift from the git tree.
        self._build_wheel()

        # _build_wheel() stashes the actual built filename so we don't
        # have to re-glob here. Falls back to the
        # constant-driven name if for any reason it wasn't set.
        local_wheel_path = getattr(self, "_actual_wheel_path", None)
        wheel_file = getattr(self, "_actual_wheel_file", None)
        if not local_wheel_path or not wheel_file:
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

        # Extract bundled assets from the wheel's site-packages to
        # /opt/netgen/ — covers the case where the operator's install
        # source tree (script_dir) is missing the support files
        # (e.g. dialog flow that only sftp'd wheel + installer; bare
        # `pip install <wheel>` directly). The wheel ALWAYS contains
        # these under site-packages/resources/dpdk/ and
        # site-packages/ostg_docker/, so we can recover from there.
        # Runs unconditionally — overwriting is safe because the wheel
        # is the canonical source for these files anyway.
        self.log(
            f"Deploying bundled assets from wheel to {INSTALL_DIR}/ "
            f"(resources/dpdk + ostg_docker + Dockerfile.frr)..."
        )
        # Pass the extraction script via Python heredoc so multi-line
        # control flow works (a single `python -c "..."` can't carry
        # `if/for` blocks). The script imports the freshly-installed
        # packages directly, finds their on-disk location via
        # __file__, and copies the trees to INSTALL_DIR. Works
        # regardless of where pip decided to install the wheel
        # (system site-packages, venv, pipx).
        wheel_extract_cmd = (
            "python3 - <<'PYEOF'\n"
            "import os, shutil\n"
            f"DEST = {INSTALL_DIR!r}\n"
            "os.makedirs(DEST, exist_ok=True)\n"
            "\n"
            "# 1. resources/dpdk — DPDK install scripts + tx_worker source\n"
            "import resources.dpdk as d\n"
            "src = os.path.dirname(d.__file__)\n"
            "dst = os.path.join(DEST, 'resources', 'dpdk')\n"
            "os.makedirs(os.path.dirname(dst), exist_ok=True)\n"
            "shutil.rmtree(dst, ignore_errors=True)\n"
            "shutil.copytree(src, dst)\n"
            "for f in os.listdir(dst):\n"
            "    if f.endswith('.sh'):\n"
            "        os.chmod(os.path.join(dst, f), 0o755)\n"
            "\n"
            "# 2. ostg_docker — Dockerfile.frr + start-frr.sh + frr.conf.template\n"
            "try:\n"
            "    import ostg_docker as o\n"
            "    src2 = os.path.dirname(o.__file__)\n"
            "    dst2 = os.path.join(DEST, 'ostg_docker')\n"
            "    shutil.rmtree(dst2, ignore_errors=True)\n"
            "    shutil.copytree(src2, dst2)\n"
            "    # 3. Convenience: publish key FRR files at the install root\n"
            "    #    too — setup_docker_frr looks here historically.\n"
            "    for f in ('Dockerfile.frr', 'start-frr.sh', 'frr.conf.template'):\n"
            "        s = os.path.join(src2, f)\n"
            "        if os.path.isfile(s):\n"
            "            d3 = os.path.join(DEST, f)\n"
            "            shutil.copy2(s, d3)\n"
            "            if f.endswith('.sh'):\n"
            "                os.chmod(d3, 0o755)\n"
            "except ImportError:\n"
            "    pass\n"
            "\n"
            "print('deployed:', sorted(os.listdir(DEST)))\n"
            "PYEOF\n"
        )
        extract_result = self.run_command(
            wheel_extract_cmd, check=False, capture_output=True,
        )
        if extract_result.returncode == 0:
            self.log(f"✓ Bundled assets deployed to {INSTALL_DIR}/")
        else:
            err = (extract_result.stderr or extract_result.stdout or "unknown").strip()[:400]
            self.log(
                f"Wheel asset extraction returned rc={extract_result.returncode}: "
                f"{err}. Falling back to script_dir copy below.",
                "WARNING",
            )

        # Legacy: also copy from script_dir if the files exist there
        # (gives full-source installs the same behavior). Either path
        # produces the same end state — the wheel extract above is
        # authoritative, the script_dir copy is a fallback for very
        # old install paths.
        #
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

        # DPDK runtime tree. The server endpoints (/api/dpdk/{status,bind,
        # unbind,interfaces}) shell out to dpdk_bind.sh; if the script
        # directory is missing they return 404. install_dpdk.sh's step 6
        # builds tx_worker from resources/dpdk/tx_worker/{tx_worker.c,
        # meson.build} — those need to be on the target too, otherwise the
        # build step fails and there's no tx_worker binary for DPDK streams.
        # Ship the whole resources/dpdk/ tree (scripts + tx_worker source)
        # excluding build artifacts and Python bytecode.
        dpdk_src_dir = os.path.join(script_dir, "resources", "dpdk")
        dpdk_remote_dir = f"{INSTALL_DIR}/resources/dpdk"
        if os.path.isdir(dpdk_src_dir):
            self.run_command(f"mkdir -p {dpdk_remote_dir}")
            # Top-level .sh helpers (dpdk_bind.sh, dpdk_start.sh,
            # dpdk_tx_worker.sh, install_dpdk.sh, verify_dpdk.sh).
            for fname in sorted(os.listdir(dpdk_src_dir)):
                if not fname.endswith(".sh"):
                    continue
                local_sh = os.path.join(dpdk_src_dir, fname)
                remote_sh = f"{dpdk_remote_dir}/{fname}"
                self.copy_file(local_sh, remote_sh)
                self.run_command(f"chmod +x {remote_sh}")

            # tx_worker source — required by install_dpdk.sh step 6.
            # Copy tx_worker.c and meson.build; skip the local build/
            # output directory (that gets created on the target during
            # the actual build).
            tx_worker_src_dir = os.path.join(dpdk_src_dir, "tx_worker")
            tx_worker_remote_dir = f"{dpdk_remote_dir}/tx_worker"
            if os.path.isdir(tx_worker_src_dir):
                self.run_command(f"mkdir -p {tx_worker_remote_dir}")
                for fname in sorted(os.listdir(tx_worker_src_dir)):
                    if fname in ("build", "__pycache__"):
                        continue
                    if fname.startswith("."):
                        continue
                    local_path = os.path.join(tx_worker_src_dir, fname)
                    if not os.path.isfile(local_path):
                        continue
                    remote_path = f"{tx_worker_remote_dir}/{fname}"
                    self.copy_file(local_path, remote_path)
                self.log(f"✓ tx_worker source deployed to {tx_worker_remote_dir}")
            else:
                self.log(
                    f"resources/dpdk/tx_worker/ not found at {tx_worker_src_dir}; "
                    f"install_dpdk.sh step 6 will fail to build tx_worker.",
                    "WARNING",
                )

            self.log(f"✓ DPDK runtime tree deployed to {dpdk_remote_dir}")

            # The wheel-installed run_tgen_server.py hardcodes the legacy path
            # /opt/OSTG/resources/dpdk/dpdk_bind.sh in its DPDK endpoints. Even
            # though INSTALL_DIR has been renamed to /opt/netgen, the server
            # still resolves DPDK scripts via /opt/OSTG/. Until that hardcoded
            # path is fixed at the source, drop a symlink so both paths point
            # at the same files.
            if INSTALL_DIR != LEGACY_INSTALL_DIR:
                self.run_command(f"mkdir -p {LEGACY_INSTALL_DIR}", check=False)
                self.run_command(
                    f"ln -sfn {INSTALL_DIR}/resources {LEGACY_INSTALL_DIR}/resources",
                    check=False,
                )
                self.log(
                    f"✓ Compatibility symlink {LEGACY_INSTALL_DIR}/resources → "
                    f"{INSTALL_DIR}/resources (server still uses legacy path)"
                )
        else:
            self.log(
                f"resources/dpdk/ not found at {dpdk_src_dir}; DPDK bind/unbind "
                "endpoints will return 404 until you deploy these manually.",
                "WARNING",
            )

        self.log(f"✓ {PRODUCT_NAME} installed successfully")

    def install_dpdk_runtime(self):
        """Install DPDK runtime: apt prereqs + (optionally) build DPDK and tx_worker.

        Runs the same install_dpdk.sh that the /admin portal's "Install DPDK"
        button kicks off, just from the install path. Skipped entirely when
        --no-dpdk was passed; build step skipped (apt-only) when
        --skip-dpdk-build was passed.

        Tolerant of failures — DPDK build can take 15+ min and depends on
        kernel headers and libibverbs versions. If install_dpdk.sh exits
        non-zero, we log a clear WARNING and continue. The /admin portal's
        "Install DPDK" button remains as the operator's manual recourse.
        """
        if not self.install_dpdk:
            self.log("DPDK install skipped (--no-dpdk).")
            return

        script = f"{INSTALL_DIR}/resources/dpdk/install_dpdk.sh"

        # Verify the script exists on the target — install_ostg() should have
        # already deployed it, but guard against partial installs.
        check = self.run_command(
            f"test -x {shlex.quote(script)} && echo OK || echo MISSING",
            check=False, capture_output=True,
        )
        out = (check.stdout or "") if hasattr(check, "stdout") else ""
        if "OK" not in out:
            self.log(
                f"DPDK install skipped: {script} not deployed. "
                f"Re-run after fixing install_ostg() or use the /admin portal.",
                "WARNING",
            )
            return

        if self.skip_dpdk_build:
            self.log("Installing DPDK apt prerequisites only (--skip-dpdk-build)...")
            env_prefix = "AUTO_MODE=1 SKIP_BUILD=1"
            phase_label = "DPDK prereqs"
            timeout_sec = 600  # 10 min for apt
        else:
            self.log("Installing DPDK runtime (apt prereqs + DPDK build + tx_worker)...")
            self.log("This will take 10-20 minutes on a fresh box. Tail progress with:")
            self.log(f"  ssh root@{self.remote_host or '<host>'} 'tail -f /var/log/netgen-install-dpdk.log'")
            env_prefix = "AUTO_MODE=1"
            phase_label = "DPDK runtime"
            timeout_sec = 1800  # 30 min cap

        # Run the script with stdout+stderr → log file on the target. We use
        # `script -qc ...` rather than > redirection so install_dpdk.sh's
        # color/progress output flushes line-by-line into the log even when
        # not on a TTY. AUTO_MODE=1 makes prompts non-interactive.
        log_path = "/var/log/netgen-install-dpdk.log"
        cmd = (
            f"{env_prefix} bash {shlex.quote(script)} --auto "
            f"> {shlex.quote(log_path)} 2>&1"
        )
        rc = self.run_command(cmd, check=False, timeout=timeout_sec)
        rc_code = rc.returncode if hasattr(rc, "returncode") else 1

        if rc_code == 0:
            self.log(f"✓ {phase_label} install completed (log: {log_path})")
            # Quick sanity check: confirm tx_worker was built (only if we
            # asked for the build).
            if not self.skip_dpdk_build:
                tx_bin = f"{INSTALL_DIR}/resources/dpdk/tx_worker/build/tx_worker"
                check = self.run_command(
                    f"test -x {shlex.quote(tx_bin)} && echo OK || echo MISSING",
                    check=False, capture_output=True,
                )
                out = (check.stdout or "") if hasattr(check, "stdout") else ""
                if "OK" in out:
                    self.log(f"✓ tx_worker binary present at {tx_bin}")
                else:
                    self.log(
                        f"DPDK install reported success but tx_worker binary "
                        f"missing at {tx_bin}. Check {log_path} for details.",
                        "WARNING",
                    )
        else:
            self.log(
                f"DPDK install exited rc={rc_code}. Continuing with the rest "
                f"of the install — netgen-server will start fine without DPDK; "
                f"streams that need it will fall back to the Scapy/kernel path.",
                "WARNING",
            )
            self.log(
                f"Diagnose with:  ssh root@{self.remote_host or '<host>'} 'tail -200 {log_path}'",
                "WARNING",
            )
            self.log(
                f"Or retry from the /admin portal once netgen-server is up.",
                "WARNING",
            )

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
            # --network=host is REQUIRED: the Alpine apk fetch inside the
            # build container must use the host's resolver. On corporate-DNS
            # hosts (e.g. Juniper internal / svl-hp-ai-srv02) docker's
            # default bridge DNS can't reach the Alpine CDN even though the
            # host can, and the build dies with repeated
            # "temporary error (try again later)" on EVERY mirror — the
            # mirror-retry loop above can't help because it's a DNS-in-the-
            # sandbox problem, not a mirror problem. Confirmed live: the
            # build only succeeds with host networking. (If this build still
            # fails, the server's startup self-heal — maybe_rebuild_frr_image
            # — will rebuild the image via the Docker SDK with the same
            # network_mode=host on first run, so FRR/DHCP still recover.)
            if use_buildx:
                return self.run_command(
                    f"docker buildx build --network=host --platform linux/amd64 -t {DOCKER_IMAGE} -f {dockerfile_path} --load{extra} {build_context}",
                    check=False, timeout=600
                )
            return self.run_command(
                f"docker build --network=host -t {DOCKER_IMAGE} -f {dockerfile_path}{extra} {build_context}",
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

        # Docker network creation removed — FRR containers run on
        # host networking, the bridge was created but never attached.

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
        """Start (or restart) Netgen services.

        Critical subtlety: `systemctl start <unit>` is a no-op when the
        unit is already active — meaning an upgrade install would
        leave the OLD process running with the OLD code in memory,
        even though the new wheel was pip-installed to disk. Operators
        hit this and were confused why `pip3 show ostg-trafficgen`
        reported the new version but the running server still behaved
        like the old one.

        Fix: probe is-active first. If already running, do `restart`
        (forcibly reloads the process); else do `start` (cold start).
        Both paths log what they did so the operator can see the
        decision in the install transcript.
        """
        self.log(f"Starting {PRODUCT_NAME} services...")

        # Stop any straggling non-systemd processes that might be holding
        # the listen socket. pkill is safe — it only matches the literal
        # script name, not the systemd-managed entrypoint.
        self.run_command("pkill -f run_tgen_server.py", check=False)

        # Decide between start (cold) and restart (already-active).
        # Capture stdout so we can distinguish "active" from
        # "inactive"/"failed"/"activating" cleanly.
        pre = self.run_command(
            "systemctl is-active netgen-server.service",
            check=False, capture_output=True,
        )
        pre_state = (pre.stdout or "").strip() if hasattr(pre, "stdout") else ""

        if pre_state == "active":
            self.log(
                "netgen-server already active — restarting so the new wheel "
                "loads in memory (start would be a no-op on a live unit)"
            )
            start_cmd = "systemctl restart netgen-server.service"
        else:
            self.log(f"netgen-server is '{pre_state or 'unknown'}' — starting cold")
            start_cmd = "systemctl start netgen-server.service"

        start_result = self.run_command(start_cmd, check=False)
        if start_result.returncode != 0:
            self.log(
                f"{start_cmd} returned non-zero; checking status...",
                "WARNING",
            )

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

        # v0.3.16+: pre-flight self-heal — same as install_local().
        # See _heal_dpkg_state docstring for full rationale.
        self._heal_dpkg_state()

        # Run installation steps
        self.cleanup_old_install()
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        # DPDK runtime — apt prereqs + build + tx_worker. After install_ostg
        # so resources/dpdk/install_dpdk.sh is already deployed. Tolerant of
        # failures: if DPDK build fails, netgen-server still runs and the
        # /admin portal exposes a manual retry button.
        self.install_dpdk_runtime()
        self.install_ai_dependencies()
        self.install_ollama()
        self.setup_docker_frr()
        self.create_systemd_services()
        self.start_ostg_services()
        self.verify_installation()
        self.test_frr_functionality()

    def _heal_dpkg_state(self):
        """Auto-recover from a half-configured dpkg state left by a
        prior aborted install.

        Context: when a previous install hit the dpkg conffile prompt
        (the bug we fixed in v0.3.16) and bailed, apt left packages
        stuck in 'iU' / 'iF' / half-configured limbo. Subsequent
        ``apt-get install`` calls on those packages fail with
        ``Internal Error, No file name for <pkg>:amd64`` because
        apt's cache is out of sync with dpkg's state. The package
        manager can't recover from this with ``--reinstall`` alone —
        the half-state must be cleared first via
        ``dpkg --remove --force-all`` before apt can install fresh.

        This pre-flight detects the half-state and recovers
        automatically so the operator never has to SSH in and run
        recovery commands manually. Observed on svl-d-ai-srv04 in
        the field — operator had a half-installed docker stack from
        a pre-v0.3.16 install and netgen-server.service refused to
        start (Requires=docker.service failed).

        Recovery sequence:
          1. ``dpkg --audit`` — what's half-configured?
          2. Force-remove the docker stack (most likely culprit)
          3. ``dpkg --configure -a --force-confdef --force-confold`` —
             complete any other half-states non-interactively
          4. ``apt-get clean`` + ``apt-get update`` — refresh metadata
          5. Re-audit; warn if anything still broken but proceed
             (main install path will get one more chance to recover
             via its own --force-conf flags)

        Idempotent + side-effect-light when dpkg is clean: just an
        audit call that returns quickly with empty output.
        """
        pm = self.system_info["package_manager"]
        if pm != "apt":
            # Only apt has the conffile-prompt failure mode. dnf/yum
            # use a different prompt mechanism with its own resolution.
            return

        audit = self.run_command(
            "dpkg --audit", check=False, capture_output=True,
        )
        audit_out = ((audit.stdout or "") + (audit.stderr or "")).strip()
        if not audit_out:
            # Clean state — skip recovery entirely (the common case
            # on every install except retries after a pre-v0.3.16
            # conffile-prompt failure).
            self.log("dpkg state clean (no half-configured packages)")
            return

        self.log(
            "Detected packages in half-configured state from a prior "
            "aborted install — auto-recovering before proceeding...",
            "WARNING",
        )
        self.log(f"dpkg --audit:\n{audit_out}", "WARNING")

        # Step 1: clean apt cache so it can re-fetch on the next install.
        # The cache mismatch is what produces "Internal Error, No file
        # name for <pkg>".
        self._wait_for_apt_lock()
        self.run_command("apt-get clean", check=False)

        # Step 2: force-remove the docker stack. Safe because
        # install_docker() will reinstall it cleanly later — the goal
        # here is to clear the half-state, not preserve any of it.
        # If docker isn't in the audit, this is a fast no-op.
        docker_pkgs = (
            "containerd.io docker-ce docker-ce-cli "
            "docker-buildx-plugin docker-ce-rootless-extras "
            "docker-compose-plugin"
        )
        self.log("Removing half-configured docker stack (will reinstall)...")
        self.run_command(
            f"dpkg --remove --force-all {docker_pkgs}",
            check=False,
        )

        # Step 3: tell dpkg to finish configuring anything else that's
        # stuck. The --force-confdef + --force-confold flags suppress
        # the original conffile prompt that aborted the prior install.
        self.run_command(
            "DEBIAN_FRONTEND=noninteractive dpkg --configure -a "
            "--force-confdef --force-confold",
            check=False,
        )

        # Step 4: refresh apt metadata so the upcoming installs can
        # find packages cleanly.
        self._wait_for_apt_lock()
        self.run_command("apt-get update", check=False)

        # Step 5: re-audit and log final state. If anything still
        # stuck, surface a WARNING but proceed — install_system_
        # dependencies / install_docker each have their own retry
        # logic that may pick up the slack.
        audit2 = self.run_command(
            "dpkg --audit", check=False, capture_output=True,
        )
        out2 = ((audit2.stdout or "") + (audit2.stderr or "")).strip()
        if out2:
            self.log(
                "Some packages still half-configured after recovery — "
                "main install path will attempt to finish them:\n" + out2,
                "WARNING",
            )
        else:
            self.log("✓ dpkg state recovered cleanly", "INFO")

    def install_local(self):
        """Install Netgen locally."""
        self.log(f"Installing {PRODUCT_NAME} locally...")

        # Check if running as root
        if os.geteuid() != 0:
            self.log("This script must be run as root for local installation", "ERROR")
            sys.exit(1)

        # v0.3.16+: pre-flight self-heal. Detects + recovers from a
        # half-configured dpkg state left by a prior aborted install
        # (the conffile-prompt bug we fixed in this release). Without
        # this, a retry of Fresh Install on a previously-failed host
        # would fail again on `apt-get install` because of stuck
        # packages — even though the v0.3.16 conffile fix would have
        # prevented the original failure. Auto-recovery means the
        # operator can just click Fresh Install again and the
        # installer fixes the prior mess.
        self._heal_dpkg_state()

        # Run installation steps
        self.cleanup_old_install()
        self.install_system_dependencies()
        self.install_python_dependencies()
        self.install_docker()
        self.install_ostg()
        # DPDK runtime — apt prereqs + build + tx_worker. After install_ostg
        # so resources/dpdk/install_dpdk.sh is already deployed. Tolerant of
        # failures: if DPDK build fails, netgen-server still runs and the
        # /admin portal exposes a manual retry button.
        self.install_dpdk_runtime()
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
    parser.add_argument(
        "--no-dpdk",
        dest="install_dpdk",
        action="store_false",
        default=True,
        help="Skip the DPDK runtime install step entirely. Default is to "
             "install it. Pass this on hosts that won't generate traffic "
             "(devbox-only, no DPDK-capable NIC).",
    )
    parser.add_argument(
        "--skip-dpdk-build",
        action="store_true",
        default=False,
        help="Install DPDK apt prerequisites only — don't compile DPDK or "
             "tx_worker. Useful when DPDK is already installed system-wide "
             "and you just want netgen-server's apt deps in place. The "
             "tx_worker binary won't be built; you can build it later from "
             "the /admin portal.",
    )
    parser.add_argument(
        "-w", "--wheel",
        dest="wheel",
        default=None,
        help="Path to a pre-built ostg_trafficgen-*.whl. Skips the "
             "`python -m build` step entirely. Required when running on "
             "a host with no source tree (e.g. the client GUI's Fresh "
             "Install dialog sftp-copies the wheel + this script into "
             "/tmp/netgen_install/ and has nothing to build from).",
    )

    args = parser.parse_args()

    if args.host and not args.password:
        print("Error: Password is required for remote installation. Use -p or --password option.")
        sys.exit(1)

    installer = NetgenInstaller(
        remote_host=args.host,
        remote_user=args.user,
        remote_pass=args.password,
        install_dpdk=args.install_dpdk,
        skip_dpdk_build=args.skip_dpdk_build,
        prebuilt_wheel=args.wheel,
    )

    installer.run()


if __name__ == "__main__":
    main()
