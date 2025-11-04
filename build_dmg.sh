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

# Configuration
OSTG_VERSION="0.1.52"
DMG_NAME="OSTG-TrafficGenerator-${OSTG_VERSION}.dmg"

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
rm -rf build/ dist_macos/ "OSTG Client.app" "OSTG Server.app"
rm -rf build_image/dist_macos/

# Exclude backup folder from build
log "Excluding backup folder from packaging..."

# Build the macOS apps
log "Building macOS applications..."

# Build client app
log "Building OSTG Client app..."
MACOS_BUILD=1 pyinstaller -y ostg_client.spec

# Check if apps were built in dist/ and move them to build_image/dist_macos/
if [[ -d "dist/OSTG Client.app" ]]; then
    log "Moving OSTG Client.app to build_image/dist_macos/"
    mkdir -p build_image/dist_macos
    mv "dist/OSTG Client.app" "build_image/dist_macos/"
    # Remove the empty OSTG Client directory if it exists
    [[ -d "dist/OSTG Client" ]] && rm -rf "dist/OSTG Client"
fi

if [[ ! -d "build_image/dist_macos/OSTG Client.app" ]]; then
    error "Failed to build OSTG Client app"
fi

# Build server app
log "Building OSTG Server app..."
MACOS_BUILD=1 pyinstaller -y ostg_server.spec

# Check if server app was built in dist/ and move it to dist_macos/
if [[ -d "dist/OSTG Server.app" ]]; then
    log "Moving OSTG Server.app to build_image/dist_macos/"
    mv "dist/OSTG Server.app" "build_image/dist_macos/"
fi

if [[ ! -d "build_image/dist_macos/OSTG Server.app" ]]; then
    error "Failed to build OSTG Server app"
fi

# Get app sizes
CLIENT_SIZE=$(du -sh "build_image/dist_macos/OSTG Client.app" | cut -f1)
SERVER_SIZE=$(du -sh "build_image/dist_macos/OSTG Server.app" | cut -f1)

log "✓ OSTG Client.app built ($CLIENT_SIZE)"
log "✓ OSTG Server.app built ($SERVER_SIZE)"

# Create quick DMG installer
log "Creating DMG installer..."

# Create temporary directory for DMG contents
DMG_DIR="OSTG_QUICK_DMG_TEMP"
rm -rf "$DMG_DIR"
mkdir "$DMG_DIR"

# Create README for temp directory
cat > "$DMG_DIR/README.md" << 'EOF'
# OSTG DMG Temporary Directory

This is a temporary directory created during the DMG build process.

## Contents
- OSTG Client.app - macOS client application
- OSTG Server.app - macOS server application
- README.txt - Installation instructions

## Note
This directory is automatically cleaned up after DMG creation.
EOF

# Copy the apps to the DMG directory
cp -R "build_image/dist_macos/OSTG Client.app" "$DMG_DIR/"
cp -R "build_image/dist_macos/OSTG Server.app" "$DMG_DIR/"

# Create a simple README
cat > "$DMG_DIR/README.txt" << EOF
OSTG Traffic Generator v${OSTG_VERSION}

Quick Start:
1. Drag OSTG Client.app to Applications folder
2. Drag OSTG Server.app to Applications folder
3. Launch OSTG Client.app to start the GUI
4. Launch OSTG Server.app to start the server

Requirements:
- macOS 10.13 or later
- Docker Desktop (for FRR containers)

For full installation with dependencies, use the complete installer.
EOF

# Create Applications shortcut
ln -s /Applications "$DMG_DIR/Applications"

# Create the DMG in build_image directory
mkdir -p build_image
hdiutil create -volname "OSTG Traffic Generator" \
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
log "   - OSTG Client.app ($CLIENT_SIZE)"
log "   - OSTG Server.app ($SERVER_SIZE)"
log ""
log "🚀 To use:"
log "   1. open 'build_image/$DMG_NAME'"
log "   2. Drag apps to Applications folder"
log "   3. Launch from Applications"
log ""
log "💡 For complete installation with dependencies, use:"
log "   ./build_macos_installer.sh"
