#!/bin/bash
#
# install_client.sh — Client-only install (operator laptop / dev machine).
#
# For the operator who has a netgen-server running somewhere else
# (lab box, VM, cloud) and just needs the PyQt5 GUI on their laptop.
# No Docker, no DPDK, no systemd unit — just the wheel + a launcher.
#
# Detects macOS / Linux / WSL automatically. Builds a per-user venv
# under ~/.netgen-client so the system Python stays untouched.
#
# Usage:
#   ./install_client.sh                         # uses dist/*.whl
#   ./install_client.sh path/to/the-wheel.whl   # use a specific wheel
#   ./install_client.sh -s http://lab-box:5050  # set default server URL
#   ./install_client.sh --upgrade               # force re-pip the wheel
#
# Notes:
#   * Does NOT need root. If you `sudo` it the venv lands in /root
#     and the launcher is system-wide; not what most people want.
#   * On Linux you need a desktop environment for the GUI to render.
#     The Flask /admin UI is reachable from any browser if you can't
#     run PyQt5.

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${HOME}/.netgen-client"
DEFAULT_SERVER_URL=""
WHEEL_PATH=""
FORCE_UPGRADE=false

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'
log()  { echo -e "${GREEN}[client] $1${NC}"; }
warn() { echo -e "${YELLOW}[client] WARNING: $1${NC}"; }
err()  { echo -e "${RED}[client] ERROR: $1${NC}" >&2; exit 1; }
info() { echo -e "${BLUE}[client] $1${NC}"; }

# ───────────────────────────────────────────────────────────────────
# argv parsing
# ───────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)
            DEFAULT_SERVER_URL="$2"; shift 2 ;;
        -u|--upgrade)
            FORCE_UPGRADE=true; shift ;;
        -h|--help)
            sed -n '2,25p' "$0"; exit 0 ;;
        *.whl)
            WHEEL_PATH="$1"; shift ;;
        *)
            err "Unknown argument: $1 (try --help)" ;;
    esac
done

# Pick the wheel: explicit > dist/*.whl in repo > error
if [[ -z "$WHEEL_PATH" ]]; then
    WHEEL_PATH=$(ls -t "$REPO_ROOT"/dist/ostg_trafficgen-*-py3-none-any.whl 2>/dev/null | head -1 || true)
    if [[ -z "$WHEEL_PATH" ]]; then
        WHEEL_PATH=$(ls -t "$REPO_ROOT"/ostg_trafficgen-*-py3-none-any.whl 2>/dev/null | head -1 || true)
    fi
fi
[[ -z "$WHEEL_PATH" || ! -f "$WHEEL_PATH" ]] && \
    err "No wheel found. Build one with: python3 -m build --wheel"

log "Using wheel: $WHEEL_PATH"

# ───────────────────────────────────────────────────────────────────
# OS detection
# ───────────────────────────────────────────────────────────────────

OS="unknown"
case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)
        if grep -qi microsoft /proc/version 2>/dev/null; then
            OS="wsl"
        else
            OS="linux"
        fi
        ;;
esac
info "Detected OS: $OS"
if [[ "$OS" == "wsl" ]]; then
    warn "WSL: the PyQt5 GUI needs an X server (VcXsrv / WSLg). The Flask"
    warn "/admin web UI is the easier alternative if Xorg isn't set up."
fi

# ───────────────────────────────────────────────────────────────────
# Python ≥ 3.9 check
# ───────────────────────────────────────────────────────────────────

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" &>/dev/null; then
        # Confirm the version is ≥ 3.9 — `python3` could be 3.8 on
        # older boxes.
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    cat <<EOF >&2

  Need Python ≥ 3.9 on PATH. Install options:

    macOS:    brew install python@3.12
    Ubuntu:   sudo apt-get install python3.11 python3.11-venv
    Fedora:   sudo dnf install python3.11

EOF
    err "No suitable Python interpreter."
fi

info "Using Python: $PYTHON_BIN ($("$PYTHON_BIN" --version))"

# ───────────────────────────────────────────────────────────────────
# Venv setup
# ───────────────────────────────────────────────────────────────────

if [[ -d "$VENV_DIR" && "$FORCE_UPGRADE" == "false" ]]; then
    info "Reusing existing venv: $VENV_DIR (pass --upgrade to rebuild)"
else
    if [[ -d "$VENV_DIR" ]]; then
        log "Removing existing venv for clean upgrade…"
        rm -rf "$VENV_DIR"
    fi
    log "Creating venv: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

log "Upgrading pip…"
pip install --quiet --upgrade pip setuptools wheel

log "Installing $WHEEL_PATH…"
# --no-deps is intentional? No — the client genuinely needs Flask
# (for the docker/types fakes), scapy, PyQt5, etc. Let pip resolve.
pip install --force-reinstall "$WHEEL_PATH"

# Verify the GUI entry point landed.
if ! command -v ostg-client &>/dev/null; then
    err "ostg-client not on PATH after install — wheel is missing the entry point?"
fi

# ───────────────────────────────────────────────────────────────────
# Launcher
# ───────────────────────────────────────────────────────────────────

LAUNCH_ARGS=""
if [[ -n "$DEFAULT_SERVER_URL" ]]; then
    LAUNCH_ARGS="-s $DEFAULT_SERVER_URL"
fi

case "$OS" in
    macos)
        # Drop a thin shell-wrapper that activates the venv + launches.
        # A full .app bundle (with .icns + Info.plist) is what
        # build_dmg.sh produces — this script avoids PyInstaller and
        # gives operators the same UX via a 5-line shell script.
        WRAPPER="${HOME}/Applications/Netgen Client.command"
        mkdir -p "$(dirname "$WRAPPER")"
        cat > "$WRAPPER" <<EOF
#!/bin/bash
source "$VENV_DIR/bin/activate"
exec ostg-client $LAUNCH_ARGS
EOF
        chmod +x "$WRAPPER"
        info "Launcher: $WRAPPER"
        info "Double-click it from Finder, or run 'ostg-client$([[ -n "$LAUNCH_ARGS" ]] && echo " $LAUNCH_ARGS")' after sourcing the venv."
        ;;
    linux|wsl)
        DESKTOP_DIR="${HOME}/.local/share/applications"
        mkdir -p "$DESKTOP_DIR"
        DESKTOP_FILE="$DESKTOP_DIR/netgen-client.desktop"

        # Find an icon if the wheel shipped one — XDG launchers without
        # icons render as a generic question mark in most menus.
        ICON_PATH=""
        for guess in \
            "$VENV_DIR/lib/python"*/site-packages/resources/icons/ostg.png \
            "$VENV_DIR/lib/python"*/site-packages/resources/icons/icon.png; do
            if [[ -f "$guess" ]]; then
                ICON_PATH="$guess"
                break
            fi
        done

        cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Netgen Client
GenericName=Network Traffic Generator
Comment=Netgen GUI client
Exec=$VENV_DIR/bin/ostg-client $LAUNCH_ARGS
Terminal=false
Categories=Network;System;
EOF
        [[ -n "$ICON_PATH" ]] && echo "Icon=$ICON_PATH" >> "$DESKTOP_FILE"
        chmod 644 "$DESKTOP_FILE"
        info "Desktop entry: $DESKTOP_FILE"

        command -v update-desktop-database &>/dev/null && \
            update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
        ;;
esac

# ───────────────────────────────────────────────────────────────────
# What now?
# ───────────────────────────────────────────────────────────────────

cat <<EOF

${GREEN}═══════════════════════════════════════════════════════════════════${NC}
${GREEN}  Client install complete${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

Venv:      ${BLUE}$VENV_DIR${NC}
Wheel:     ${BLUE}$WHEEL_PATH${NC}
Python:    ${BLUE}$("$PYTHON_BIN" --version)${NC}
$([[ -n "$DEFAULT_SERVER_URL" ]] && echo "Default server: ${BLUE}$DEFAULT_SERVER_URL${NC}")

Launch the GUI:
EOF

case "$OS" in
    macos)
        echo "  • Finder → ~/Applications → 'Netgen Client.command'"
        echo "  • Or shell: ${BLUE}source $VENV_DIR/bin/activate && ostg-client${NC}"
        ;;
    linux|wsl)
        echo "  • Application menu → Network → Netgen Client"
        echo "  • Or shell: ${BLUE}$VENV_DIR/bin/ostg-client$([[ -n "$LAUNCH_ARGS" ]] && echo " $LAUNCH_ARGS")${NC}"
        ;;
esac

cat <<EOF

Authentication:
  If the server has auth enabled, export the bearer token before launching:
    ${BLUE}export NETGEN_AUTH_TOKEN=<your-token>${NC}
  The client auto-injects it into every REST call.

To remove the client later:
  ${BLUE}rm -rf $VENV_DIR  &&  rm -f $WRAPPER${NC}

EOF
