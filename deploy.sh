#!/bin/bash

# Generic OSTG Deployment Script
# This script handles deployment of OSTG project changes to remote server
# Usage: ./deploy.sh [options]

set -e

# Configuration (can be overridden by command line arguments)
SERVER_HOST="${SERVER_HOST:-svl-hp-ai-srv04}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_PASS="${SERVER_PASS:-Embe1mpls}"
SERVER_PATH="${SERVER_PATH:-/opt/OSTG}"
TEMP_PATH="${TEMP_PATH:-/tmp/ostg_deploy_temp}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Default options
DEPLOY_TYPE="full"          # full, wheel-only, source-only, config-only
BACKUP_ENABLED="true"       # true, false
VERIFY_INSTALL="true"       # true, false
START_SERVER="true"         # true, false
CLEAN_TEMP="true"          # true, false
FORCE_REBUILD="false"      # true, false

# Function definitions
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

debug() {
    echo -e "${CYAN}[$(date +'%Y-%m-%d %H:%M:%S')] DEBUG: $1${NC}"
}

success() {
    echo -e "${PURPLE}[$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS: $1${NC}"
}

# Show usage information
show_usage() {
    cat << EOF
Generic OSTG Deployment Script

Usage: $0 [OPTIONS]

OPTIONS:
    -t, --type TYPE        Deployment type (full|wheel-only|source-only|config-only)
                          full: Complete rebuild and deployment (default)
                          wheel-only: Deploy only the wheel package
                          source-only: Deploy only source code changes
                          config-only: Deploy only configuration files
    
    -H, --host HOST        Target server hostname or IP address
    --server HOST          Alias for --host
    -u, --user USER        SSH username
    -p, --pass PASS        SSH password
    -P, --path PATH        Remote installation path
    -T, --temp-path PATH   Remote temporary path
    
    -n, --no-backup       Skip creating backup of current installation
    -v, --no-verify       Skip installation verification
    -s, --no-start        Don't start server after deployment
    -c, --no-clean        Don't clean temporary files
    -f, --force-rebuild   Force rebuild even if no changes detected
    -h, --help            Show this help message

EXAMPLES:
    $0                                          # Full deployment with default server
    $0 -t wheel-only                           # Deploy only wheel package
    $0 -H myserver.com -u admin -p mypass      # Deploy to custom server
    $0 -t source-only -n -v                    # Deploy without backup and verification
    $0 -f -H 192.168.1.100 -u root -p secret  # Force rebuild to specific server

ENVIRONMENT VARIABLES:
    SERVER_HOST           Target server hostname (default: svl-hp-ai-srv04)
    SERVER_USER           SSH username (default: root)
    SERVER_PASS           SSH password (default: Embe1mpls)
    SERVER_PATH           Remote installation path (default: /opt/OSTG)
    TEMP_PATH             Remote temporary path (default: /tmp/ostg_deploy_temp)

EOF
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -t|--type)
                DEPLOY_TYPE="$2"
                shift 2
                ;;
            -H|--host|--server)
                SERVER_HOST="$2"
                shift 2
                ;;
            -u|--user)
                SERVER_USER="$2"
                shift 2
                ;;
            -p|--pass)
                SERVER_PASS="$2"
                shift 2
                ;;
            -P|--path)
                SERVER_PATH="$2"
                shift 2
                ;;
            -T|--temp-path)
                TEMP_PATH="$2"
                shift 2
                ;;
            -n|--no-backup)
                BACKUP_ENABLED="false"
                shift
                ;;
            -v|--no-verify)
                VERIFY_INSTALL="false"
                shift
                ;;
            -s|--no-start)
                START_SERVER="false"
                shift
                ;;
            -c|--no-clean)
                CLEAN_TEMP="false"
                shift
                ;;
            -f|--force-rebuild)
                FORCE_REBUILD="true"
                shift
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                error "Unknown option: $1. Use --help for usage information."
                ;;
        esac
    done
}

# Validate deployment type
validate_deploy_type() {
    case $DEPLOY_TYPE in
        full|wheel-only|source-only|config-only)
            info "Deployment type: $DEPLOY_TYPE"
            ;;
        *)
            error "Invalid deployment type: $DEPLOY_TYPE. Use: full, wheel-only, source-only, config-only"
            ;;
    esac
}

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."
    
    # Check if we're in the right directory
    if [[ ! -f "pyproject.toml" ]]; then
        error "pyproject.toml not found. Please run this script from the OSTG project root directory."
    fi
    
    # Check if Python is available
    if ! command -v python3 &> /dev/null; then
        error "python3 not found. Please install Python 3."
    fi
    
    # Check if sshpass is available
    # Check for sshpass in common locations
    SSHPASS_CMD=""
    if command -v sshpass &> /dev/null; then
        SSHPASS_CMD="sshpass"
    elif [ -f "/opt/homebrew/bin/sshpass" ]; then
        SSHPASS_CMD="/opt/homebrew/bin/sshpass"
    elif [ -f "/usr/local/bin/sshpass" ]; then
        SSHPASS_CMD="/usr/local/bin/sshpass"
    else
        warn "sshpass not found. You may need to install it for automated SSH."
        warn "On macOS: brew install hudochenkov/sshpass/sshpass"
        warn "On Ubuntu: sudo apt-get install sshpass"
    fi
    
    # Check if wheel file exists (for wheel-only and full deployments)
    if [[ "$DEPLOY_TYPE" == "wheel-only" || "$DEPLOY_TYPE" == "full" ]]; then
        WHEEL_FILE="build_image/ostg_trafficgen-0.1.52-py3-none-any.whl"
        if [[ ! -f "$WHEEL_FILE" ]]; then
            # Fallback to root directory
            WHEEL_FILE="ostg_trafficgen-0.1.52-py3-none-any.whl"
        fi
        if [[ ! -f "$WHEEL_FILE" ]]; then
            if [[ "$FORCE_REBUILD" == "true" ]]; then
                info "Wheel file not found, rebuilding..."
                ./rebuild_quick.sh
            else
                error "Wheel file not found. Run './rebuild_quick.sh' first or use --force-rebuild"
            fi
        fi
    fi
    
    success "Prerequisites check completed"
}

# Rebuild project if needed
rebuild_if_needed() {
    if [[ "$DEPLOY_TYPE" == "full" ]]; then
        WHEEL_FILE="build_image/ostg_trafficgen-0.1.52-py3-none-any.whl"
        if [[ ! -f "$WHEEL_FILE" ]]; then
            WHEEL_FILE="ostg_trafficgen-0.1.52-py3-none-any.whl"
        fi
        if [[ "$FORCE_REBUILD" == "true" || ! -f "$WHEEL_FILE" ]]; then
            log "Rebuilding project..."
            ./rebuild_quick.sh
            success "Project rebuilt successfully"
        else
            info "Using existing wheel file"
        fi
    fi
}

# Copy files to server
copy_files_to_server() {
    log "Copying files to server..."
    
    case $DEPLOY_TYPE in
        full|wheel-only|source-only)
            debug "Copying wheel package..."
            WHEEL_FILE="build_image/ostg_trafficgen-0.1.52-py3-none-any.whl"
            if [[ ! -f "$WHEEL_FILE" ]]; then
                WHEEL_FILE="ostg_trafficgen-0.1.52-py3-none-any.whl"
            fi
            $SSHPASS_CMD -p "$SERVER_PASS" scp "$WHEEL_FILE" "$SERVER_USER@$SERVER_HOST:$SERVER_PATH/"
            ;;
        config-only)
            debug "Copying configuration files only..."
            $SSHPASS_CMD -p "$SERVER_PASS" scp *.conf *.json *.yaml *.yml 2>/dev/null || true
            $SSHPASS_CMD -p "$SERVER_PASS" scp -r config/ 2>/dev/null || true
            ;;
    esac
    
    success "Files copied to server"
}

# Execute deployment on server
deploy_on_server() {
    log "Executing deployment on server..."
    
    $SSHPASS_CMD -p "$SERVER_PASS" ssh "$SERVER_USER@$SERVER_HOST" << EOF
        set -e
        
        log() {
            echo -e "\033[0;32m[\$(date +'%Y-%m-%d %H:%M:%S')] \$1\033[0m"
        }
        
        warn() {
            echo -e "\033[1;33m[\$(date +'%Y-%m-%d %H:%M:%S')] WARNING: \$1\033[0m"
        }
        
        info() {
            echo -e "\033[0;34m[\$(date +'%Y-%m-%d %H:%M:%S')] INFO: \$1\033[0m"
        }
        
        success() {
            echo -e "\033[0;35m[\$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS: \$1\033[0m"
        }
        
        # Ensure target directory exists
        mkdir -p $SERVER_PATH
        
        # Stop existing server processes
        log "Stopping existing OSTG server processes..."
        pkill -f ostg-server || true
        pkill -f run_tgen_server.py || true
        systemctl stop ostg-server 2>/dev/null || true
        sleep 2
        
        # Create backup if enabled
        if [[ "$BACKUP_ENABLED" == "true" && -d "$SERVER_PATH" ]]; then
            log "Creating backup of current installation..."
            backup_dir="$SERVER_PATH_backup_\$(date +%Y%m%d_%H%M%S)"
            cp -r "$SERVER_PATH" "\$backup_dir" || warn "Backup creation failed"
            info "Backup created: \$backup_dir"
        fi
        
        # Change to target directory
        cd "$SERVER_PATH"
        
        # Deploy based on type
        case "$DEPLOY_TYPE" in
            full)
                log "Performing full deployment..."
                # Install wheel package to /opt/OSTG (dependencies will also be installed there)
                log "Installing wheel package to $SERVER_PATH..."
                pip3 install --target $SERVER_PATH --force-reinstall $SERVER_PATH/ostg_trafficgen-0.1.52-py3-none-any.whl
                ;;
            wheel-only)
                log "Installing wheel package to $SERVER_PATH..."
                pip3 install --target $SERVER_PATH --force-reinstall $SERVER_PATH/ostg_trafficgen-0.1.52-py3-none-any.whl
                ;;
            source-only)
                log "Source files are part of the wheel package. Installing to $SERVER_PATH..."
                pip3 install --target $SERVER_PATH --force-reinstall $SERVER_PATH/ostg_trafficgen-0.1.52-py3-none-any.whl
                ;;
            config-only)
                log "Updating configuration files only..."
                # Copy config files if they exist
                cp $TEMP_PATH/*.conf . 2>/dev/null || true
                cp $TEMP_PATH/*.json . 2>/dev/null || true
                cp $TEMP_PATH/*.yaml . 2>/dev/null || true
                cp $TEMP_PATH/*.yml . 2>/dev/null || true
                cp -r $TEMP_PATH/config/ . 2>/dev/null || true
                ;;
        esac
        
        # Create .pth file to ensure Python can find modules in /opt/OSTG
        log "Configuring Python path..."
        PYTHON_PTH="/usr/local/lib/python3.10/dist-packages/opt_ostg.pth"
        echo "$SERVER_PATH" > \$PYTHON_PTH 2>/dev/null || {
            info "Creating .pth file failed, will update systemd service instead"
        }
        
        # Determine the correct Python path based on installation structure
        # With --target, packages are installed to $SERVER_PATH/lib/python3.10/site-packages
        PYTHON_LIB_PATH="$SERVER_PATH/lib/python3.10/site-packages"
        if [[ ! -d "\$PYTHON_LIB_PATH" ]]; then
            # Fallback: if using direct installation, use $SERVER_PATH
            PYTHON_LIB_PATH="$SERVER_PATH"
        fi
        
        # Extract and copy files from wheel to ensure we have the latest versions
        log "Extracting files from wheel..."
        cd $SERVER_PATH
        python3 -m zipfile -e ostg_trafficgen-0.1.52-py3-none-any.whl /tmp/wheel_extract 2>/dev/null || unzip -q ostg_trafficgen-0.1.52-py3-none-any.whl -d /tmp/wheel_extract 2>/dev/null || true
        
        # Copy run_tgen_server.py
        if [[ -f "/tmp/wheel_extract/run_tgen_server.py" ]]; then
            cp /tmp/wheel_extract/run_tgen_server.py $SERVER_PATH/run_tgen_server.py
            info "Copied run_tgen_server.py from wheel"
        elif [[ -f "\$PYTHON_LIB_PATH/run_tgen_server.py" ]]; then
            cp \$PYTHON_LIB_PATH/run_tgen_server.py $SERVER_PATH/run_tgen_server.py
            info "Copied run_tgen_server.py from site-packages"
        else
            warn "run_tgen_server.py not found in wheel"
        fi
        
        # Copy utils directory files (ensure latest version)
        if [[ -d "/tmp/wheel_extract/utils" ]]; then
            mkdir -p $SERVER_PATH/utils
            cp -r /tmp/wheel_extract/utils/* $SERVER_PATH/utils/
            info "Copied utils directory from wheel"
        elif [[ -d "\$PYTHON_LIB_PATH/utils" ]]; then
            mkdir -p $SERVER_PATH/utils
            cp -r \$PYTHON_LIB_PATH/utils/* $SERVER_PATH/utils/
            info "Copied utils directory from site-packages"
        fi
        
        # Copy widgets directory files (ensure latest version for server-side functions)
        if [[ -d "/tmp/wheel_extract/widgets" ]]; then
            mkdir -p $SERVER_PATH/widgets
            cp -r /tmp/wheel_extract/widgets/* $SERVER_PATH/widgets/
            info "Copied widgets directory from wheel"
        elif [[ -d "\$PYTHON_LIB_PATH/widgets" ]]; then
            mkdir -p $SERVER_PATH/widgets
            cp -r \$PYTHON_LIB_PATH/widgets/* $SERVER_PATH/widgets/
            info "Copied widgets directory from site-packages"
        fi
        
        rm -rf /tmp/wheel_extract 2>/dev/null || true
        
        # Update systemd service file to use /opt/OSTG and set PYTHONPATH
        log "Updating systemd service file..."
        SYSTEMD_SERVICE="/etc/systemd/system/ostg-server.service"
        
            # Update service file (use PYTHONPATH so modules can be found)
            # With --target, modules are in $SERVER_PATH/lib/python3.10/site-packages
            # PYTHONPATH should include this directory so Python can find the modules
            # run_tgen_server.py is in $SERVER_PATH
            cat > \$SYSTEMD_SERVICE << SERVICEEOF
[Unit]
Description=OSTG Traffic Generator Server
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$SERVER_PATH
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=\$PYTHON_LIB_PATH:$SERVER_PATH:/usr/local/lib/python3.10/dist-packages"
ExecStart=/usr/bin/python3 $SERVER_PATH/run_tgen_server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ostg-server

[Install]
WantedBy=multi-user.target
SERVICEEOF
        
        # Reload systemd daemon
        systemctl daemon-reload
        info "Systemd service file updated to use $SERVER_PATH with PYTHONPATH including \$PYTHON_LIB_PATH"
        
        # Verify installation if enabled
        if [[ "$VERIFY_INSTALL" == "true" ]]; then
            log "Verifying installation..."
            export PYTHONPATH="\$PYTHON_LIB_PATH:$SERVER_PATH:/usr/local/lib/python3.10/dist-packages"
            python3 -c "import ostg; print('OSTG imported successfully')" || warn "Import test failed"
            python3 -c "import utils; print('Utils imported successfully')" || warn "Utils import test failed"
            
            # Check command line tools
            if command -v ostg-server &> /dev/null; then
                info "ostg-server command available"
            else
                warn "ostg-server command not found"
            fi
        fi
        
        # Start server if enabled
        if [[ "$START_SERVER" == "true" ]]; then
            log "Starting OSTG server using systemd..."
            # Try to start systemd service first
            if systemctl start ostg-server 2>/dev/null; then
                sleep 3
                if systemctl is-active --quiet ostg-server; then
                    success "OSTG server started successfully via systemd"
                    info "Service status: \$(systemctl is-active ostg-server)"
                else
                    warn "systemd service failed, starting manually..."
                    nohup ostg-server --port 5051 > ostg_server.log 2>&1 &
                    sleep 3
                fi
            else
                warn "systemd service not available, starting manually..."
                nohup ostg-server --port 5051 > ostg_server.log 2>&1 &
                sleep 3
            fi
            
            # Check if server is running
            if pgrep -f ostg-server > /dev/null; then
                success "OSTG server started successfully"
                info "Server PID: \$(pgrep -f ostg-server)"
            else
                warn "Failed to start OSTG server"
            fi
            
            # Check if server is listening on port 5051
            if netstat -tlnp 2>/dev/null | grep -q ":5051"; then
                info "Server is listening on port 5051"
            else
                warn "Server not listening on port 5051"
            fi
        fi
        
        # Clean up wheel file if enabled
        if [[ "$CLEAN_TEMP" == "true" ]]; then
            log "Cleaning up wheel file..."
            rm -f $SERVER_PATH/ostg_trafficgen-0.1.52-py3-none-any.whl
        fi
        
        success "Deployment completed successfully!"
EOF
    
    success "Server deployment completed"
}

# Show server configuration
show_server_config() {
    log "Server Configuration"
    echo "==================="
    info "Server Host: $SERVER_HOST"
    info "SSH User: $SERVER_USER"
    info "Target Path: $SERVER_PATH"
    info "Temp Path: $TEMP_PATH"
    echo ""
}

# Show deployment summary
show_summary() {
    log "Deployment Summary"
    echo "=================="
    info "Deployment Type: $DEPLOY_TYPE"
    info "Server: $SERVER_HOST"
    info "Target Path: $SERVER_PATH"
    info "Backup Enabled: $BACKUP_ENABLED"
    info "Verification Enabled: $VERIFY_INSTALL"
    info "Server Auto-start: $START_SERVER"
    info "Clean Temp Files: $CLEAN_TEMP"
    echo ""
    
    if [[ "$START_SERVER" == "true" ]]; then
        success "OSTG server should be running on: http://$SERVER_HOST:5051"
    fi
    
    echo ""
    info "Next steps:"
    info "1. Connect to OSTG client: http://$SERVER_HOST:5051"
    info "2. Test your changes"
    info "3. Check server logs: ssh $SERVER_USER@$SERVER_HOST 'journalctl -u ostg-server -f'"
    echo ""
}

# Main execution
main() {
    log "Starting Generic OSTG Deployment Script"
    echo "========================================"
    
    # Parse command line arguments
    parse_args "$@"
    
    # Validate inputs
    validate_deploy_type
    
    # Show server configuration
    show_server_config
    
    # Run deployment steps
    check_prerequisites
    rebuild_if_needed
    copy_files_to_server
    deploy_on_server
    show_summary
    
    success "Generic deployment completed successfully!"
}

# Run main function with all arguments
main "$@"
