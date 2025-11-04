#!/bin/bash

# OSTG Project Wheel Rebuild Script
# This script rebuilds the OSTG project wheel package with BGP timer fixes

set -e

# Configuration
PROJECT_NAME="ostg-trafficgen"
VERSION="0.1.52"
WHEEL_FILE="${PROJECT_NAME//-/_}-${VERSION}-py3-none-any.whl"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Check if we're in the right directory
if [[ ! -f "pyproject.toml" ]]; then
    error "pyproject.toml not found. Please run this script from the OSTG project root directory."
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    error "python3 not found. Please install Python 3."
fi

log "Starting OSTG wheel rebuild process..."

# Step 1: Clean previous builds
log "Cleaning previous build artifacts..."
rm -rf build/ dist/ *.egg-info/
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Step 2: Verify BGP timer fixes are present
log "Verifying BGP timer fixes are present..."
if grep -q "neighbor.*timers.*keepalive.*hold_time" utils/frr_docker.py; then
    log "✓ BGP timer configuration found in frr_docker.py"
else
    warn "BGP timer configuration not found in frr_docker.py"
fi

if grep -q "bgp_keepalive.*bgp_hold_time\|keepalive.*bgp_config\|hold_time.*bgp_config" run_tgen_server.py; then
    log "✓ BGP timer mapping found in run_tgen_server.py"
else
    warn "BGP timer mapping not found in run_tgen_server.py"
fi

# Step 3: Install/update build dependencies
log "Installing/updating build dependencies..."
pip3 install --upgrade pip setuptools wheel build

# Step 4: Install project dependencies
log "Installing project dependencies..."
if [[ -f "requirements.txt" ]]; then
    pip3 install -r requirements.txt
else
    warn "requirements.txt not found, skipping dependency installation"
fi

# Step 5: Verify project structure
log "Verifying project structure..."
required_files=("pyproject.toml" "run_tgen_server.py" "run_tgen_client.py" "utils/" "ostg/" "traffic_client/")
for file in "${required_files[@]}"; do
    if [[ -e "$file" ]]; then
        log "✓ $file found"
    else
        error "$file not found - project structure incomplete"
    fi
done

# Step 6: Build the wheel
log "Building wheel package..."
python3 -m build

# Step 7: Verify build output
log "Verifying build output..."
if [[ -f "dist/$WHEEL_FILE" ]]; then
    log "✓ Wheel file created: dist/$WHEEL_FILE"
    ls -la dist/
else
    error "Wheel file not created: dist/$WHEEL_FILE"
fi

# Step 8: Test wheel installation
log "Testing wheel installation..."
pip3 uninstall -y "$PROJECT_NAME" 2>/dev/null || true
pip3 install "dist/$WHEEL_FILE"

# Step 9: Verify installation
log "Verifying installation..."
if python3 -c "import ostg; print('OSTG imported successfully')" 2>/dev/null; then
    log "✓ OSTG package imports successfully"
else
    warn "OSTG package import test failed"
fi

# Step 10: Check for command line tools
log "Checking command line tools..."
if command -v ostg-server &> /dev/null; then
    log "✓ ostg-server command available"
else
    warn "ostg-server command not found in PATH"
fi

if command -v ostg-client &> /dev/null; then
    log "✓ ostg-client command available"
else
    warn "ostg-client command not found in PATH"
fi

# Step 11: Create backup of old wheel if it exists
if [[ -f "$WHEEL_FILE" ]]; then
    log "Creating backup of existing wheel file..."
    mv "$WHEEL_FILE" "${WHEEL_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Step 12: Copy wheel to build_image directory for organized storage
log "Copying wheel to build_image directory..."
mkdir -p build_image
cp "dist/$WHEEL_FILE" build_image/

# Step 13: Generate deployment info
log "Generating deployment information..."
cat > rebuild_info.txt << EOF
OSTG Wheel Rebuild Information
=============================
Build Date: $(date)
Wheel File: $WHEEL_FILE
Version: $VERSION
Python Version: $(python3 --version)
Build Directory: $(pwd)

Files Modified (BGP Timer Fixes):
- utils/frr_docker.py: Fixed command generation for BGP timers
- utils/bgp.py: Fixed missing remove_result assignment
- run_tgen_server.py: Added timer mapping in BGP configuration

Deployment Commands:
1. Copy wheel to server: scp $WHEEL_FILE root@svl-hp-ai-srv02:/root/OSTG/
2. SSH to server: ssh root@svl-hp-ai-srv02
3. Install wheel: pip3 install $WHEEL_FILE --force-reinstall
4. Start server: python3 run_tgen_server.py &

Testing BGP Timer Configuration:
1. Configure BGP with custom timers (Keepalive: 20, Hold-time: 40)
2. Click "Apply BGP" in OSTG client
3. Check logs for timer values
4. Verify in FRR container: docker exec -it <container> vtysh -c "show running-config" | grep timers
EOF

log "✓ Rebuild information saved to rebuild_info.txt"

# Step 14: Final verification
log "Performing final verification..."
if [[ -f "build_image/$WHEEL_FILE" && -f "dist/$WHEEL_FILE" ]]; then
    log "✓ Wheel file available in both build_image and dist directories"
    
    # Show file sizes
    build_image_size=$(du -h "build_image/$WHEEL_FILE" | cut -f1)
    dist_size=$(du -h "dist/$WHEEL_FILE" | cut -f1)
    log "✓ Build image wheel size: $build_image_size"
    log "✓ Dist wheel size: $dist_size"
else
    error "Wheel file verification failed"
fi

log "🎉 OSTG wheel rebuild completed successfully!"
log "Wheel file: build_image/$WHEEL_FILE"
log "Ready for deployment to server"

# Optional: Show next steps
echo ""
info "Next steps:"
info "1. Deploy to server: ./deploy.sh"
info "2. Or manually copy: scp build_image/$WHEEL_FILE root@svl-hp-ai-srv02:/root/OSTG/"
info "3. Test BGP timer configuration on server"
info "4. Check rebuild_info.txt for detailed information"
