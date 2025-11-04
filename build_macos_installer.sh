#!/bin/bash

# OSTG macOS Installer Builder
# This script creates a complete macOS DMG installer with all packages included

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Configuration
OSTG_VERSION="0.1.52"
INSTALLER_NAME="OSTG-TrafficGenerator-${OSTG_VERSION}"
DMG_NAME="${INSTALLER_NAME}-macOS.dmg"
VENV_NAME="ostg_build_env"
BUILD_DIR="macos_build"
DIST_DIR="dist"

# Logging functions
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

success() {
    echo -e "${PURPLE}[$(date +'%Y-%m-%d %H:%M:%S')] ✓ $1${NC}"
}

# Check if we're on macOS
check_macos() {
    if [[ "$OSTYPE" != "darwin"* ]]; then
        error "This script is designed for macOS only"
    fi
    log "✓ macOS detected: $(sw_vers -productName) $(sw_vers -productVersion)"
}

# Check dependencies
check_dependencies() {
    log "Checking build dependencies..."
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        error "Python 3 is required but not installed"
    fi
    success "Python $(python3 --version) found"
    
    # Check pip
    if ! command -v pip3 &> /dev/null; then
        error "pip3 is required but not installed"
    fi
    success "pip3 found"
    
    # Check hdiutil (for DMG creation)
    if ! command -v hdiutil &> /dev/null; then
        error "hdiutil is required but not installed"
    fi
    success "hdiutil found"
    
    # Check if we can create virtual environments
    if ! python3 -m venv --help &> /dev/null; then
        error "Python venv module not available"
    fi
    success "Python venv module available"
}

# Setup build environment
setup_build_env() {
    log "Setting up build environment..."
    
    # Clean previous builds
    if [[ -d "$BUILD_DIR" ]]; then
        warn "Removing previous build directory..."
        rm -rf "$BUILD_DIR"
    fi
    
    if [[ -d "$VENV_NAME" ]]; then
        warn "Removing previous virtual environment..."
        rm -rf "$VENV_NAME"
    fi
    
    # Create build directory
    mkdir -p "$BUILD_DIR"
    
    # Create virtual environment
    log "Creating virtual environment..."
    python3 -m venv "$VENV_NAME"
    
    # Activate virtual environment
    source "$VENV_NAME/bin/activate"
    
    # Upgrade pip and install build tools
    log "Installing build tools..."
    pip install --upgrade pip setuptools wheel build
    
    # Install PyInstaller and Pillow
    log "Installing PyInstaller and dependencies..."
    pip install pyinstaller Pillow
    
    # Install project dependencies
    if [[ -f "requirements.txt" ]]; then
        log "Installing project dependencies..."
        pip install -r requirements.txt
    else
        warn "requirements.txt not found, skipping dependency installation"
    fi
    
    success "Build environment ready"
}

# Build the wheel package
build_wheel() {
    log "Building wheel package..."
    
    # Clean previous builds
    rm -rf build/ dist/ *.egg-info/
    find . -name "*.pyc" -delete
    find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    
    # Build the wheel
    python -m build --wheel
    
    # Check if wheel was created
    WHEEL_FILE=$(find dist/ -name "*.whl" | head -1)
    if [[ -z "$WHEEL_FILE" ]]; then
        error "Failed to build wheel package"
    fi
    
    success "Wheel package created: $(basename "$WHEEL_FILE")"
    echo "$WHEEL_FILE"
}

# Build macOS applications
build_apps() {
    log "Building macOS applications..."
    
    # Build client app
    log "Building OSTG Client app..."
    MACOS_BUILD=1 pyinstaller -y ostg_client.spec
    
    if [[ ! -d "dist_macos/OSTG Client.app" ]]; then
        error "Failed to build OSTG Client app"
    fi
    success "OSTG Client app built"
    
    # Build server app
    log "Building OSTG Server app..."
    MACOS_BUILD=1 pyinstaller -y ostg_server.spec
    
    if [[ ! -d "dist_macos/OSTG Server.app" ]]; then
        error "Failed to build OSTG Server app"
    fi
    success "OSTG Server app built"
}

# Create installer package
create_installer_package() {
    log "Creating installer package..."
    
    # Create installer directory structure
    INSTALLER_DIR="$BUILD_DIR/$INSTALLER_NAME"
    mkdir -p "$INSTALLER_DIR"
    
    # Copy applications
    log "Copying applications..."
    cp -R "dist_macos/OSTG Client.app" "$INSTALLER_DIR/"
    cp -R "dist_macos/OSTG Server.app" "$INSTALLER_DIR/"
    
    # Copy wheel package
    WHEEL_FILE=$(find dist/ -name "*.whl" | head -1)
    if [[ -n "$WHEEL_FILE" ]]; then
        cp "$WHEEL_FILE" "$INSTALLER_DIR/"
        success "Wheel package included: $(basename "$WHEEL_FILE")"
    fi
    
    # Create installation script
    create_install_script "$INSTALLER_DIR"
    
    # Create uninstall script
    create_uninstall_script "$INSTALLER_DIR"
    
    # Create README
    create_readme "$INSTALLER_DIR"
    
    # Copy additional resources
    if [[ -d "resources" ]]; then
        cp -R resources "$INSTALLER_DIR/"
        success "Resources included"
    fi
    
    # Copy systemd services
    if [[ -d "systemd" ]]; then
        cp -R systemd "$INSTALLER_DIR/"
        success "Systemd services included"
    fi
    
    # Copy Docker files
    if [[ -f "Dockerfile.frr" ]]; then
        cp Dockerfile.frr "$INSTALLER_DIR/"
        success "Docker configuration included"
    fi
    
    if [[ -f "frr.conf.template" ]]; then
        cp frr.conf.template "$INSTALLER_DIR/"
        success "FRR configuration template included"
    fi
    
    # Create Applications shortcut
    ln -s /Applications "$INSTALLER_DIR/Applications"
    
    success "Installer package created"
}

# Create installation script
create_install_script() {
    local INSTALLER_DIR="$1"
    
    cat > "$INSTALLER_DIR/install_ostg.sh" << 'EOF'
#!/bin/bash

# OSTG Traffic Generator Installation Script for macOS
# This script installs OSTG with all dependencies

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

# Check if running as admin
check_admin() {
    if [[ $EUID -eq 0 ]]; then
        warn "Running as root. This is not recommended for GUI applications."
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Install Homebrew if not present
install_homebrew() {
    if ! command -v brew &> /dev/null; then
        log "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        
        # Add Homebrew to PATH
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f "/usr/local/bin/brew" ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    else
        log "Homebrew already installed"
    fi
}

# Install system dependencies
install_system_deps() {
    log "Installing system dependencies..."
    
    # Install Python 3.10+ if not present
    if ! command -v python3 &> /dev/null; then
        brew install python@3.11
    fi
    
    # Install Docker
    if ! command -v docker &> /dev/null; then
        log "Installing Docker Desktop..."
        brew install --cask docker
        warn "Please start Docker Desktop and log in before continuing"
        read -p "Press Enter when Docker is ready..."
    else
        log "Docker already installed"
    fi
    
    # Install other dependencies
    brew install git curl wget
}

# Install OSTG
install_ostg() {
    log "Installing OSTG Traffic Generator..."
    
    # Create installation directory
    INSTALL_DIR="$HOME/Applications/OSTG"
    mkdir -p "$INSTALL_DIR"
    
    # Copy applications
    cp -R "OSTG Client.app" "$INSTALL_DIR/"
    cp -R "OSTG Server.app" "$INSTALL_DIR/"
    
    # Copy wheel package if present
    WHEEL_FILE=$(find . -name "*.whl" | head -1)
    if [[ -n "$WHEEL_FILE" ]]; then
        cp "$WHEEL_FILE" "$INSTALL_DIR/"
        log "Wheel package installed: $(basename "$WHEEL_FILE")"
    fi
    
    # Copy resources
    if [[ -d "resources" ]]; then
        cp -R resources "$INSTALL_DIR/"
    fi
    
    if [[ -d "systemd" ]]; then
        cp -R systemd "$INSTALL_DIR/"
    fi
    
    if [[ -f "Dockerfile.frr" ]]; then
        cp Dockerfile.frr "$INSTALL_DIR/"
    fi
    
    if [[ -f "frr.conf.template" ]]; then
        cp frr.conf.template "$INSTALL_DIR/"
    fi
    
    # Create desktop shortcuts (if on desktop)
    if [[ -d "$HOME/Desktop" ]]; then
        ln -sf "$INSTALL_DIR/OSTG Client.app" "$HOME/Desktop/OSTG Client"
        ln -sf "$INSTALL_DIR/OSTG Server.app" "$HOME/Desktop/OSTG Server"
    fi
    
    log "OSTG installed to: $INSTALL_DIR"
}

# Main installation
main() {
    log "Starting OSTG Traffic Generator installation..."
    
    check_admin
    install_homebrew
    install_system_deps
    install_ostg
    
    log ""
    log "🎉 Installation completed successfully!"
    log ""
    log "📱 Applications installed:"
    log "   - OSTG Client.app (GUI client)"
    log "   - OSTG Server.app (Server application)"
    log ""
    log "🚀 To start OSTG:"
    log "   1. Open 'OSTG Client.app' from Applications or Desktop"
    log "   2. Or run server: open '$HOME/Applications/OSTG/OSTG Server.app'"
    log ""
    log "📋 Next steps:"
    log "   1. Start Docker Desktop"
    log "   2. Launch OSTG Client"
    log "   3. Configure your network interfaces"
    log "   4. Set up BGP neighbors"
}

main "$@"
EOF
    
    chmod +x "$INSTALLER_DIR/install_ostg.sh"
    success "Installation script created"
}

# Create uninstall script
create_uninstall_script() {
    local INSTALLER_DIR="$1"
    
    cat > "$INSTALLER_DIR/uninstall_ostg.sh" << 'EOF'
#!/bin/bash

# OSTG Traffic Generator Uninstall Script for macOS

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

# Main uninstall
main() {
    log "Starting OSTG Traffic Generator uninstallation..."
    
    INSTALL_DIR="$HOME/Applications/OSTG"
    
    if [[ -d "$INSTALL_DIR" ]]; then
        log "Removing OSTG installation..."
        rm -rf "$INSTALL_DIR"
        success "OSTG removed from: $INSTALL_DIR"
    else
        warn "OSTG installation not found at: $INSTALL_DIR"
    fi
    
    # Remove desktop shortcuts
    if [[ -L "$HOME/Desktop/OSTG Client" ]]; then
        rm "$HOME/Desktop/OSTG Client"
        log "Removed desktop shortcut: OSTG Client"
    fi
    
    if [[ -L "$HOME/Desktop/OSTG Server" ]]; then
        rm "$HOME/Desktop/OSTG Server"
        log "Removed desktop shortcut: OSTG Server"
    fi
    
    # Remove from Applications folder if present
    if [[ -d "/Applications/OSTG Client.app" ]]; then
        rm -rf "/Applications/OSTG Client.app"
        log "Removed: /Applications/OSTG Client.app"
    fi
    
    if [[ -d "/Applications/OSTG Server.app" ]]; then
        rm -rf "/Applications/OSTG Server.app"
        log "Removed: /Applications/OSTG Server.app"
    fi
    
    log ""
    log "🎉 OSTG Traffic Generator uninstalled successfully!"
    log ""
    log "Note: Docker Desktop and other system dependencies were not removed."
    log "You can remove them manually if not needed for other applications."
}

main "$@"
EOF
    
    chmod +x "$INSTALLER_DIR/uninstall_ostg.sh"
    success "Uninstall script created"
}

# Create README
create_readme() {
    local INSTALLER_DIR="$1"
    
    cat > "$INSTALLER_DIR/README.md" << EOF
# OSTG Traffic Generator v${OSTG_VERSION}

Advanced Network Traffic Generator with BGP/OSPF Support for macOS

## 📦 Package Contents

- **OSTG Client.app** - GUI client application
- **OSTG Server.app** - Server application
- **install_ostg.sh** - Installation script
- **uninstall_ostg.sh** - Uninstallation script
- **ostg_trafficgen-${OSTG_VERSION}-py3-none-any.whl** - Python wheel package
- **resources/** - Application resources and icons
- **systemd/** - Service configuration files
- **Dockerfile.frr** - FRR Docker configuration
- **frr.conf.template** - FRR configuration template

## 🚀 Quick Start

### Option 1: GUI Applications (Recommended)
1. Double-click **OSTG Client.app** to launch the GUI client
2. Double-click **OSTG Server.app** to launch the server

### Option 2: Full Installation
1. Run the installation script:
   \`\`\`bash
   ./install_ostg.sh
   \`\`\`
2. Follow the prompts to install dependencies
3. Launch applications from Applications folder or Desktop

## 📋 System Requirements

- **macOS**: 10.13 (High Sierra) or later
- **Architecture**: Intel x64 or Apple Silicon (ARM64)
- **Memory**: 4GB RAM minimum, 8GB recommended
- **Storage**: 2GB free space
- **Network**: Ethernet or WiFi interface for traffic generation

## 🔧 Dependencies

The installation script will automatically install:
- **Python 3.11+** (via Homebrew)
- **Docker Desktop** (for FRR containers)
- **Git, curl, wget** (development tools)

## 🎯 Features

- **BGP Traffic Generation** - Generate BGP route advertisements
- **OSPF Support** - OSPF neighbor simulation
- **Packet Capture** - Real-time packet analysis
- **Network Simulation** - Multiple device simulation
- **GUI Interface** - User-friendly PyQt5 interface
- **Docker Integration** - FRR routing daemon in containers

## 📖 Usage

### Starting the Client
1. Launch **OSTG Client.app**
2. Connect to server (default: http://localhost:5051)
3. Configure network interfaces
4. Add BGP neighbors and route pools
5. Start traffic generation

### Starting the Server
1. Launch **OSTG Server.app**
2. Server starts on port 5051 (configurable)
3. Wait for client connections

### Command Line Usage
\`\`\`bash
# Start server
open "OSTG Server.app"

# Start client
open "OSTG Client.app"
\`\`\`

## 🔧 Configuration

### Server Configuration
- **Port**: 5051 (default)
- **Host**: 0.0.0.0 (all interfaces)
- **Logs**: Check server console output

### Client Configuration
- **Server URL**: http://localhost:5051
- **Interfaces**: Select network interfaces
- **BGP Settings**: Configure AS numbers and neighbors

## 🐳 Docker Integration

OSTG uses Docker to run FRR (Free Range Routing) containers:
- **Container**: ostg-frr:latest
- **Services**: BGP, Zebra
- **Network**: Host networking for external connectivity

## 🛠️ Troubleshooting

### Common Issues

1. **Docker not running**
   - Start Docker Desktop
   - Ensure Docker daemon is running

2. **Port already in use**
   - Change server port in OSTG Server.app
   - Or stop conflicting services

3. **Permission denied**
   - Ensure you have admin rights for Docker
   - Check network interface permissions

### Logs and Debugging
- **Server logs**: Check OSTG Server.app console
- **Client logs**: Check OSTG Client.app console
- **Docker logs**: \`docker logs <container_name>\`

## 🗑️ Uninstallation

Run the uninstall script:
\`\`\`bash
./uninstall_ostg.sh
\`\`\`

This will remove:
- OSTG applications
- Desktop shortcuts
- Installation directory

## 📞 Support

For issues and questions:
- Check the troubleshooting section
- Review application logs
- Ensure all dependencies are installed

## 📄 License

MIT License - See LICENSE file for details

---
**OSTG Traffic Generator v${OSTG_VERSION}**  
Built for macOS with ❤️
EOF
    
    success "README created"
}

# Create DMG installer
create_dmg() {
    log "Creating DMG installer..."
    
    INSTALLER_DIR="$BUILD_DIR/$INSTALLER_NAME"
    
    # Remove existing DMG
    if [[ -f "$DMG_NAME" ]]; then
        rm "$DMG_NAME"
    fi
    
    # Create DMG
    hdiutil create -volname "$INSTALLER_NAME" \
                   -srcfolder "$INSTALLER_DIR" \
                   -ov -format UDZO \
                   "$DMG_NAME"
    
    if [[ -f "$DMG_NAME" ]]; then
        DMG_SIZE=$(du -sh "$DMG_NAME" | cut -f1)
        success "DMG installer created: $DMG_NAME ($DMG_SIZE)"
    else
        error "Failed to create DMG installer"
    fi
}

# Cleanup
cleanup() {
    log "Cleaning up build artifacts..."
    
    # Remove build directory
    if [[ -d "$BUILD_DIR" ]]; then
        rm -rf "$BUILD_DIR"
    fi
    
    # Remove virtual environment
    if [[ -d "$VENV_NAME" ]]; then
        rm -rf "$VENV_NAME"
    fi
    
    # Remove PyInstaller build artifacts
    rm -rf build/ dist_macos/
    find . -name "*.spec" -not -name "ostg_*.spec" -delete 2>/dev/null || true
    
    success "Cleanup completed"
}

# Main build process
main() {
    log "🚀 Starting OSTG macOS Installer Build Process"
    log "Version: $OSTG_VERSION"
    log "Installer: $DMG_NAME"
    log ""
    
    check_macos
    check_dependencies
    setup_build_env
    
    WHEEL_FILE=$(build_wheel)
    build_apps
    create_installer_package
    create_dmg
    
    log ""
    success "🎉 Build completed successfully!"
    log ""
    log "📦 Installer Package: $DMG_NAME"
    log "📱 Applications Built:"
    log "   - OSTG Client.app"
    log "   - OSTG Server.app"
    log ""
    log "🚀 Distribution Ready:"
    log "   - Self-contained macOS applications"
    log "   - Complete installation package"
    log "   - Uninstaller included"
    log "   - Comprehensive documentation"
    log ""
    log "📋 To test the installer:"
    log "   1. open '$DMG_NAME'"
    log "   2. Run install_ostg.sh"
    log "   3. Launch OSTG Client.app"
    log ""
    
    # Ask if user wants to clean up
    read -p "Clean up build artifacts? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cleanup
    else
        log "Build artifacts preserved in: $BUILD_DIR/"
    fi
}

# Run main function
main "$@"
