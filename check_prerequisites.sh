#!/bin/bash

# OSTG macOS Build Prerequisites Checker
# This script checks if all required tools and dependencies are available

# set -e  # Disabled to allow optional checks to fail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

# Counters
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $1${NC}"
}

success() {
    echo -e "${PURPLE}[$(date +'%Y-%m-%d %H:%M:%S')] ✓ $1${NC}"
}

# Check function
check() {
    local name="$1"
    local command="$2"
    local required="$3"
    local install_help="$4"
    
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    
    if eval "$command" &> /dev/null; then
        success "$name - Available"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        if [[ "$required" == "true" ]]; then
            error "$name - MISSING (Required)"
            FAILED_CHECKS=$((FAILED_CHECKS + 1))
            if [[ -n "$install_help" ]]; then
                info "Install: $install_help"
            fi
        else
            warn "$name - Missing (Optional)"
            WARNING_CHECKS=$((WARNING_CHECKS + 1))
            if [[ -n "$install_help" ]]; then
                info "Install: $install_help"
            fi
        fi
        return 1
    fi
}

# Check macOS version
check_macos_version() {
    log "Checking macOS version..."
    
    if [[ "$OSTYPE" != "darwin"* ]]; then
        error "Not running on macOS. This script is macOS-only."
        exit 1
    fi
    
    MACOS_VERSION=$(sw_vers -productVersion)
    MACOS_MAJOR=$(echo "$MACOS_VERSION" | cut -d. -f1)
    MACOS_MINOR=$(echo "$MACOS_VERSION" | cut -d. -f2)
    
    info "macOS Version: $MACOS_VERSION"
    
    if [[ $MACOS_MAJOR -lt 10 ]] || [[ $MACOS_MAJOR -eq 10 && $MACOS_MINOR -lt 13 ]]; then
        error "macOS 10.13 (High Sierra) or later required. Current: $MACOS_VERSION"
        exit 1
    else
        success "macOS version compatible"
    fi
}

# Check system architecture
check_architecture() {
    log "Checking system architecture..."
    
    ARCH=$(uname -m)
    info "Architecture: $ARCH"
    
    if [[ "$ARCH" == "arm64" ]]; then
        success "Apple Silicon (ARM64) - Fully supported"
    elif [[ "$ARCH" == "x86_64" ]]; then
        success "Intel x64 - Fully supported"
    else
        warn "Unknown architecture: $ARCH - May have compatibility issues"
    fi
}

# Check available disk space
check_disk_space() {
    log "Checking available disk space..."
    
    # Get available space in GB
    AVAILABLE_SPACE=$(df -h . | tail -1 | awk '{print $4}' | sed 's/Gi//')
    
    if [[ -z "$AVAILABLE_SPACE" ]]; then
        # Try different format
        AVAILABLE_SPACE=$(df -h . | tail -1 | awk '{print $4}' | sed 's/G//')
    fi
    
    info "Available disk space: ${AVAILABLE_SPACE}GB"
    
    if [[ $AVAILABLE_SPACE -ge 5 ]]; then
        success "Sufficient disk space (5GB+ required)"
    else
        warn "Low disk space. Build may fail. Recommended: 5GB+ free"
    fi
}

# Check memory
check_memory() {
    log "Checking system memory..."
    
    TOTAL_MEMORY=$(sysctl -n hw.memsize)
    TOTAL_MEMORY_GB=$((TOTAL_MEMORY / 1024 / 1024 / 1024))
    
    info "Total memory: ${TOTAL_MEMORY_GB}GB"
    
    if [[ $TOTAL_MEMORY_GB -ge 8 ]]; then
        success "Sufficient memory (8GB+ recommended)"
    elif [[ $TOTAL_MEMORY_GB -ge 4 ]]; then
        warn "Minimum memory (4GB). Build may be slow. Recommended: 8GB+"
    else
        error "Insufficient memory. Minimum 4GB required, 8GB+ recommended"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
}

# Main prerequisite checks
main() {
    log "🔍 OSTG macOS Build Prerequisites Check"
    log "========================================"
    log ""
    
    # System checks
    check_macos_version
    check_architecture
    check_disk_space
    check_memory
    
    log ""
    log "📦 Checking Required Tools..."
    log "-----------------------------"
    
    # Required tools
    check "Python 3" "python3 --version" "true" "Install from python.org or use Homebrew: brew install python@3.11"
    check "pip3" "pip3 --version" "true" "Usually comes with Python 3"
    check "Git" "git --version" "true" "Install from git-scm.com or use Homebrew: brew install git"
    check "curl" "curl --version" "true" "Usually pre-installed on macOS"
    check "hdiutil" "hdiutil help" "true" "Built-in macOS tool"
    
    log ""
    log "🔧 Checking Build Tools..."
    log "--------------------------"
    
    # Build tools
    check "setuptools" "python3 -c 'import setuptools'" "true" "pip3 install setuptools"
    check "wheel" "python3 -c 'import wheel'" "true" "pip3 install wheel"
    check "build" "python3 -c 'import build'" "false" "pip3 install build"
    check "PyInstaller" "python3 -c 'import PyInstaller'" "false" "pip3 install pyinstaller"
    check "Pillow" "python3 -c 'import PIL'" "false" "pip3 install Pillow"
    
    log ""
    log "📚 Checking Project Dependencies..."
    log "----------------------------------"
    
    # Project dependencies (if virtual environment exists)
    if [[ -d "ostg_client_env" ]]; then
        info "Virtual environment found, checking installed packages..."
        source ostg_client_env/bin/activate
        
        check "PyQt5" "python -c 'import PyQt5'" "true" "pip install PyQt5"
        check "requests" "python -c 'import requests'" "true" "pip install requests"
        check "scapy" "python -c 'import scapy'" "true" "pip install scapy"
        check "docker" "python -c 'import docker'" "true" "pip install docker"
        check "flask" "python -c 'import flask'" "true" "pip install flask"
        
        deactivate
    else
        warn "Virtual environment 'ostg_client_env' not found"
        info "Run the installation script first to create the virtual environment"
    fi
    
    log ""
    log "📁 Checking Project Files..."
    log "---------------------------"
    
    # Project files
    check "run_tgen_client.py" "test -f run_tgen_client.py" "true" "Main client file missing"
    check "run_tgen_server.py" "test -f run_tgen_server.py" "true" "Main server file missing"
    check "ostg_client.spec" "test -f ostg_client.spec" "true" "PyInstaller spec file missing"
    check "ostg_server.spec" "test -f ostg_server.spec" "true" "PyInstaller spec file missing"
    check "resources/icons" "test -d resources/icons" "true" "Icons directory missing"
    check "requirements.txt" "test -f requirements.txt" "true" "Requirements file missing"
    
    log ""
    log "🎯 Checking Optional Tools..."
    log "----------------------------"
    
    # Optional tools
    check "Homebrew" "brew --version" "false" "Install from brew.sh"
    check "Docker Desktop" "docker --version" "false" "Install from docker.com"
    check "Xcode Command Line Tools" "xcode-select -p" "false" "xcode-select --install"
    check "create-dmg" "create-dmg --version" "false" "brew install create-dmg"
    
    log ""
    log "📊 Prerequisites Summary"
    log "========================"
    log "Total checks: $TOTAL_CHECKS"
    success "Passed: $PASSED_CHECKS"
    if [[ $WARNING_CHECKS -gt 0 ]]; then
        warn "Warnings: $WARNING_CHECKS"
    fi
    if [[ $FAILED_CHECKS -gt 0 ]]; then
        error "Failed: $FAILED_CHECKS"
    fi
    
    log ""
    if [[ $FAILED_CHECKS -eq 0 ]]; then
        success "🎉 All required prerequisites are met!"
        log ""
        log "🚀 You can now run the build scripts:"
        log "   ./build_dmg_quick.sh          # Quick DMG build"
        log "   ./build_macos_installer.sh    # Complete installer build"
        log ""
        if [[ $WARNING_CHECKS -gt 0 ]]; then
            warn "Some optional tools are missing but not required for basic builds"
        fi
    else
        error "❌ Some required prerequisites are missing"
        log ""
        log "🔧 Please install the missing tools and run this script again"
        log ""
        log "💡 Quick setup for missing tools:"
        log "   # Install Homebrew (if missing)"
        log "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        log ""
        log "   # Install Python and build tools"
        log "   brew install python@3.11"
        log "   pip3 install setuptools wheel build pyinstaller Pillow"
        log ""
        log "   # Install project dependencies"
        log "   pip3 install -r requirements.txt"
        exit 1
    fi
}

# Run main function
main "$@"
