#!/bin/bash

# OSTG DMG Builder
# Creates a DMG installer with the applications

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
    exit 1
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $1${NC}"
}

# Configuration. Version auto-detected from pyproject.toml — was
# previously hardcoded (0.1.52) which broke after every bump and
# caused the DMG filename to disagree with the wheel inside it.
OSTG_VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [[ -z "$OSTG_VERSION" ]]; then
    error "Could not parse version from pyproject.toml"
fi
DMG_NAME="Netgen-TrafficGenerator-${OSTG_VERSION}.dmg"

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    error "This script is designed for macOS only"
fi

log "🚀 Building DMG Installer..."

# Check if virtual environment exists
if [[ ! -d "ostg_client_env" ]]; then
    error "Virtual environment 'ostg_client_env' not found. Please run the installation first."
fi

# Activate virtual environment
log "Activating virtual environment..."
source ostg_client_env/bin/activate

# Verify PyInstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    log "Installing PyInstaller..."
    pip install pyinstaller Pillow
fi

# Clean previous builds
log "Cleaning previous builds..."
rm -rf build/ dist_macos/ "Netgen Client.app" "Netgen Server.app"
rm -rf build_image/dist_macos/

# Exclude backup folder from build
log "Excluding backup folder from packaging..."

# v0.3.16+: build the netgen wheel BEFORE PyInstaller so the spec's
# _discover_bundled_wheel() picks it up. The spec auto-includes the
# most-recent dist/ostg_trafficgen-*.whl in the .app bundle's root,
# which the Fresh Install dialog auto-populates via
# _guess_wheel_path(). Result: one-click Fresh Install for new users
# — no separate wheel download needed.
log "Building netgen wheel for bundling into the .app..."
# Clear any stale wheels so _discover_bundled_wheel can't pick up an
# old version by accident — picks the most-recent mtime which can
# be wrong if dist/ holds a leftover from a prior version.
rm -f dist/ostg_trafficgen-*.whl
# Ensure `build` is available in this venv.
if ! python -c "import build" 2>/dev/null; then
    log "Installing the 'build' package..."
    pip install build
fi
if python -m build --wheel; then
    WHEEL_FOUND=$(ls dist/ostg_trafficgen-${OSTG_VERSION}-*.whl 2>/dev/null | head -1)
    if [[ -n "$WHEEL_FOUND" ]]; then
        log "✓ wheel built: $(basename "$WHEEL_FOUND")"
    else
        warn "Wheel built but no ostg_trafficgen-${OSTG_VERSION}-*.whl in dist/"
        warn "— .app will still build but won't bundle a wheel."
    fi
else
    warn "Wheel build failed — .app will still build, but new-user"
    warn "Fresh Install will fall back to manual wheel browse."
fi

# Build the macOS apps
log "Building macOS applications..."

# Build client app
log "Building Netgen Client app..."
MACOS_BUILD=1 pyinstaller -y ostg_client.spec

# Check if apps were built in dist/ and move them to build_image/dist_macos/
if [[ -d "dist/Netgen Client.app" ]]; then
    log "Moving Netgen Client.app to build_image/dist_macos/"
    mkdir -p build_image/dist_macos
    mv "dist/Netgen Client.app" "build_image/dist_macos/"
    # Remove the empty Netgen Client directory if it exists
    [[ -d "dist/Netgen Client" ]] && rm -rf "dist/Netgen Client"
fi

if [[ ! -d "build_image/dist_macos/Netgen Client.app" ]]; then
    error "Failed to build Netgen Client app"
fi

# macOS Server.app is intentionally NOT built. Netgen-server needs
# Linux kernel features (DPDK, VRFs, iproute2 / systemd) that don't
# exist on macOS. Shipping a Server.app would mislead customers into
# thinking they can run a full Netgen server on a Mac. The DMG ships
# the client only; operators who want a server on a Mac for protocol-
# correctness testing run `pip install ostg_trafficgen-*.whl` and
# `ostg-server --no-dpdk` manually — see INSTALL.md scenario #2.

CLIENT_SIZE=$(du -sh "build_image/dist_macos/Netgen Client.app" | cut -f1)
log "✓ Netgen Client.app built ($CLIENT_SIZE)"

# Create quick DMG installer
log "Creating DMG installer..."

# Create temporary directory for DMG contents
DMG_DIR="OSTG_QUICK_DMG_TEMP"
rm -rf "$DMG_DIR"
mkdir "$DMG_DIR"

# Create README for temp directory
cat > "$DMG_DIR/README.md" << 'EOF'
# Netgen DMG Temporary Directory

Build-time scratch directory; cleaned up after DMG creation.

## Contents
- Netgen Client.app — macOS GUI client
- README.txt — install + usage notes
EOF

# Copy the client app to the DMG directory
cp -R "build_image/dist_macos/Netgen Client.app" "$DMG_DIR/"

# Create a user-facing README
cat > "$DMG_DIR/README.txt" << EOF
Netgen Traffic Generator — Client v${OSTG_VERSION}

Quick Start:
1. Drag "Netgen Client.app" to your Applications folder
2. Launch from Applications or Spotlight
3. Point it at your Netgen server URL (lab box, cloud, etc.)

The Netgen *server* is Linux-only — it depends on DPDK kernel modules,
Linux VRFs, and iproute2 commands that don't exist on macOS. Install
the server on a Linux host (see install_ostg_complete.py in the repo)
and point this client at it via the GUI's server-URL field.

Requirements:
- macOS 10.13 or later
- A reachable Netgen server (port 5050 by default)

For full install matrix + source-build paths, see INSTALL.md in the repo.
EOF

# Create Applications shortcut
ln -s /Applications "$DMG_DIR/Applications"

# Create the DMG in build_image directory
mkdir -p build_image
hdiutil create -volname "Netgen Traffic Generator" \
               -srcfolder "$DMG_DIR" \
               -ov -format UDZO \
               "build_image/$DMG_NAME"

# Clean up temporary directory
rm -rf "$DMG_DIR"

if [[ -f "build_image/$DMG_NAME" ]]; then
    DMG_SIZE=$(du -sh "build_image/$DMG_NAME" | cut -f1)
    log "✓ DMG installer created: build_image/$DMG_NAME ($DMG_SIZE)"
else
    error "Failed to create DMG installer"
fi

log ""
log "🎉 DMG build completed!"
log ""
log "📦 DMG Installer: build_image/$DMG_NAME"
log "📱 Applications:"
log "   - Netgen Client.app ($CLIENT_SIZE)"
log ""
log "🚀 To use:"
log "   1. open 'build_image/$DMG_NAME'"
log "   2. Drag Netgen Client.app to Applications folder"
log "   3. Launch from Applications"
log ""
log "💡 For complete installation with dependencies, use:"
log "   ./build_macos_installer.sh"
