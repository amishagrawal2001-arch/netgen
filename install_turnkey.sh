#!/bin/bash
#
# install_turnkey.sh — Single-host install (server + GUI on the same box).
#
# This is the install path for operators who want a lab-in-a-box: one
# Linux machine running netgen-server, with the GUI client launchable
# from the desktop / `ostg-client` shell command. No remote networking,
# no separate operator laptop.
#
# What it does, in order:
#   1. Runs install_ostg_complete.py for the full server install
#      (Python venv, Docker, FRR image, DPDK, systemd unit, AI deps).
#   2. Adds a desktop launcher for ostg-client (XDG .desktop file).
#   3. Verifies both entry points (`ostg-server`, `ostg-client`) are on
#      PATH and importable.
#   4. Prints concise "what now?" steps the operator can copy-paste.
#
# Compared to install_ostg_complete.py alone: this script adds the
# desktop-integration piece (XDG launcher) and runs a final verify
# pass so the operator knows the GUI is ready without separately
# checking.
#
# Usage:
#   sudo ./install_turnkey.sh
#   sudo ./install_turnkey.sh --no-dpdk          # devbox without DPDK NIC
#   sudo ./install_turnkey.sh --skip-dpdk-build  # apt deps only

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[turnkey] $1${NC}"; }
warn() { echo -e "${YELLOW}[turnkey] WARNING: $1${NC}"; }
err()  { echo -e "${RED}[turnkey] ERROR: $1${NC}" >&2; exit 1; }
info() { echo -e "${BLUE}[turnkey] $1${NC}"; }

# ───────────────────────────────────────────────────────────────────
# Pre-flight
# ───────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    err "Run as root — installs system packages, Docker, and systemd units."
fi

OS_RELEASE="$(cat /etc/os-release 2>/dev/null | grep -E '^ID=' | head -1 | cut -d= -f2 | tr -d '"' || echo unknown)"
case "$OS_RELEASE" in
    ubuntu|debian|rhel|centos|rocky|fedora)
        info "Detected $OS_RELEASE — supported."
        ;;
    *)
        warn "Distro $OS_RELEASE not in tested list (ubuntu/debian/rhel/centos/rocky/fedora)."
        warn "Continuing anyway, but expect breakage."
        ;;
esac

# Find a Python ≥ 3.9. install_ostg_complete.py uses Python so it has
# to exist; if it doesn't we bail with a clear message.
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[[ -z "$PYTHON_BIN" ]] && err "No python3 found on PATH. apt-get install python3."

log "Using Python interpreter: $PYTHON_BIN"

# ───────────────────────────────────────────────────────────────────
# Step 1: full server install
# ───────────────────────────────────────────────────────────────────

log "═══ Step 1/3: Running full server install (install_ostg_complete.py) ═══"
cd "$REPO_ROOT"
"$PYTHON_BIN" install_ostg_complete.py "$@" || err "Server install failed — see logs above."

# ───────────────────────────────────────────────────────────────────
# Step 2: desktop launcher for the GUI
# ───────────────────────────────────────────────────────────────────

log "═══ Step 2/3: Adding desktop launcher for ostg-client ═══"

# Find the icon if it ships in the wheel — best-effort.
ICON_PATH=""
for guess in \
    /opt/netgen/resources/icons/ostg.png \
    /opt/netgen/resources/icons/icon.png \
    /opt/netgen/resources/ostg.png \
    /opt/OSTG/resources/icons/ostg.png; do
    if [[ -f "$guess" ]]; then
        ICON_PATH="$guess"
        break
    fi
done

# Resolve ostg-client absolute path. The wheel's entry-point script
# lives next to the python it was installed against.
CLIENT_BIN="$(command -v ostg-client || true)"
if [[ -z "$CLIENT_BIN" ]]; then
    # Fall back to whatever the install-script put under /opt/netgen.
    if [[ -x /opt/netgen/venv/bin/ostg-client ]]; then
        CLIENT_BIN=/opt/netgen/venv/bin/ostg-client
    elif [[ -x /opt/OSTG/venv/bin/ostg-client ]]; then
        CLIENT_BIN=/opt/OSTG/venv/bin/ostg-client
    fi
fi

if [[ -z "$CLIENT_BIN" ]]; then
    warn "ostg-client not on PATH and no venv installation found."
    warn "GUI launcher not created — run 'ostg-client' manually after activating the install venv."
else
    DESKTOP_DIR=/usr/share/applications
    mkdir -p "$DESKTOP_DIR"
    DESKTOP_FILE="$DESKTOP_DIR/netgen-client.desktop"

    # Server URL the launcher will point at — local installs talk to
    # 127.0.0.1, no operator config required.
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Netgen Client
GenericName=Network Traffic Generator
Comment=Netgen GUI client — connects to local netgen-server on port 5050
Exec=$CLIENT_BIN -s http://127.0.0.1:5050
Terminal=false
Categories=Network;System;
EOF
    if [[ -n "$ICON_PATH" ]]; then
        echo "Icon=$ICON_PATH" >> "$DESKTOP_FILE"
    fi
    chmod 644 "$DESKTOP_FILE"
    info "Desktop entry: $DESKTOP_FILE → $CLIENT_BIN"

    # Refresh the desktop database so the launcher shows up in
    # GNOME / KDE / XFCE menus without a logout.
    command -v update-desktop-database &>/dev/null && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# ───────────────────────────────────────────────────────────────────
# Step 3: verify both entry points
# ───────────────────────────────────────────────────────────────────

log "═══ Step 3/3: Verifying entry points ═══"

# Server: systemd unit should be active OR at least loadable.
if systemctl is-active netgen-server.service &>/dev/null; then
    info "netgen-server.service ✓ active"
elif systemctl is-enabled netgen-server.service &>/dev/null; then
    info "netgen-server.service ✓ enabled (not running yet — start with: systemctl start netgen-server)"
elif systemctl is-enabled ostg-server.service &>/dev/null; then
    info "ostg-server.service ✓ enabled (legacy name — start with: systemctl start ostg-server)"
else
    warn "Neither netgen-server nor ostg-server systemd unit found enabled."
    warn "Check install_ostg_complete.py output for systemd-setup errors."
fi

# Client: just confirm the entry-point script exists; we can't launch
# the GUI from this (probably) headless install script.
if [[ -x "$CLIENT_BIN" ]]; then
    info "ostg-client ✓ installed at $CLIENT_BIN"
else
    warn "ostg-client entry point not found — GUI install may have failed."
fi

# ───────────────────────────────────────────────────────────────────
# What now?
# ───────────────────────────────────────────────────────────────────

cat <<EOF

${GREEN}═══════════════════════════════════════════════════════════════════${NC}
${GREEN}  Turnkey install complete${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

Server:
  Status:  ${BLUE}systemctl status netgen-server${NC}
  Logs:    ${BLUE}journalctl -u netgen-server -f${NC}
  Health:  ${BLUE}curl http://127.0.0.1:5050/api/health${NC}

Client:
  Launch from menu: ${BLUE}Applications → Network → Netgen Client${NC}
  Or from shell:    ${BLUE}ostg-client -s http://127.0.0.1:5050${NC}

First time:
  1. Wait ~10s for the server to come up
  2. Open the GUI → it pre-connects to localhost:5050
  3. Devices tab → Add Device → pick a template → Apply
  4. Streams tab → Add Stream → pick a template → Start

Need auth? Set NETGEN_AUTH_TOKEN in /etc/systemd/system/netgen-server.service.d/auth.conf
and the same env var in your shell before launching ostg-client.

EOF
