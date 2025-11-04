# OSTG Deployment System

This directory contains a comprehensive deployment system for the OSTG (Open Source Traffic Generator) project.

## 🚀 Quick Start

### Simple Deployment
```bash
# Interactive deployment menu
./deploy_quick.sh

# Or direct deployment
./deploy.sh
```

## 📁 Deployment Scripts

### 1. `deploy.sh` - Main Deployment Script
The comprehensive deployment script that handles all deployment scenarios.

**Usage:**
```bash
./deploy.sh [OPTIONS]
```

**Options:**
- `-t, --type TYPE` - Deployment type (`full`, `wheel-only`, `source-only`, `config-only`)
- `-n, --no-backup` - Skip creating backup
- `-v, --no-verify` - Skip installation verification
- `-s, --no-start` - Don't start server after deployment
- `-c, --no-clean` - Don't clean temporary files
- `-f, --force-rebuild` - Force rebuild even if no changes detected
- `-h, --help` - Show help message

**Examples:**
```bash
./deploy.sh                          # Full deployment
./deploy.sh -t wheel-only            # Deploy only wheel package
./deploy.sh -t source-only           # Deploy only source code changes
./deploy.sh -n -v                    # Deploy without backup and verification
./deploy.sh -f                       # Force rebuild and full deployment
```

### 2. `deploy_quick.sh` - Interactive Deployment
A user-friendly wrapper that provides an interactive menu for common deployment scenarios.

**Usage:**
```bash
./deploy_quick.sh
```

**Menu Options:**
1. Full Deployment (rebuild + deploy everything)
2. Source Code Only (deploy code changes)
3. Wheel Package Only (deploy built package)
4. Configuration Only (deploy config files)
5. Force Rebuild & Deploy (rebuild even if no changes)
6. Deploy Without Backup (faster deployment)
7. Custom deployment with options

### 3. `rebuild_wheel.sh` - Comprehensive Rebuild
Detailed rebuild script with validation and logging.

**Usage:**
```bash
./rebuild_wheel.sh
```

### 4. `rebuild_quick.sh` - Fast Rebuild
Quick rebuild script for rapid development cycles.

**Usage:**
```bash
./rebuild_quick.sh
```

## 🔧 Configuration

### Environment Variables
You can override default settings using environment variables:

```bash
export SERVER_HOST="your-server.com"
export SERVER_USER="your-username"
export SERVER_PASS="your-password"
export SERVER_PATH="/opt/OSTG"
```

### Configuration File
Edit `deploy_config.conf` to customize deployment settings:

```ini
# Server Configuration
SERVER_HOST=svl-hp-ai-srv02
SERVER_USER=root
SERVER_PASS=Embe1mpls
SERVER_PATH=/opt/OSTG

# Deployment Options
DEFAULT_DEPLOY_TYPE=full
BACKUP_ENABLED=true
VERIFY_INSTALL=true
START_SERVER=true
```

## 📋 Deployment Types

### 1. Full Deployment (`full`)
- Rebuilds the project if needed
- Deploys wheel package
- Updates source files
- Creates backup
- Verifies installation
- Starts server

### 2. Wheel-Only Deployment (`wheel-only`)
- Deploys only the wheel package
- Faster for package-only changes
- No source file updates

### 3. Source-Only Deployment (`source-only`)
- Updates only source code files
- Useful for code changes without rebuilding
- Faster than full deployment

### 4. Configuration-Only Deployment (`config-only`)
- Deploys only configuration files
- Fastest deployment option
- For config changes only

## 🛠️ Prerequisites

### Local Requirements
- Python 3.7+
- `sshpass` for automated SSH
- OSTG project in current directory

### Installing sshpass
```bash
# macOS
brew install hudochenkov/sshpass/sshpass

# Ubuntu/Debian
sudo apt-get install sshpass

# CentOS/RHEL
sudo yum install sshpass
```

### Server Requirements
- Python 3.7+
- SSH access
- Write permissions to target directory

## 📊 Deployment Process

### 1. Pre-deployment Checks
- Validates project structure
- Checks Python installation
- Verifies SSH connectivity
- Validates deployment type

### 2. Build Process (if needed)
- Cleans previous builds
- Installs dependencies
- Builds wheel package
- Validates build

### 3. File Transfer
- Copies files to server
- Handles different file types
- Manages temporary files

### 4. Server Deployment
- Stops existing services
- Creates backups (if enabled)
- Updates files
- Installs packages
- Verifies installation

### 5. Service Management
- Starts services (if enabled)
- Verifies service status
- Checks port availability
- Cleans temporary files

## 🔍 Troubleshooting

### Common Issues

#### 1. SSH Connection Failed
```bash
# Test SSH connection
sshpass -p "password" ssh user@host "echo 'Connection successful'"

# Check SSH key authentication
ssh -o PasswordAuthentication=no user@host
```

#### 2. Permission Denied
```bash
# Check file permissions
ls -la deploy.sh
chmod +x deploy.sh
```

#### 3. Wheel Package Not Found
```bash
# Rebuild wheel package
./rebuild_quick.sh
```

#### 4. Server Not Starting
```bash
# Check server logs
ssh user@host "tail -f /path/to/ostg_server.log"

# Check if port is in use
ssh user@host "netstat -tlnp | grep :5051"
```

### Debug Mode
Enable debug output by setting environment variable:
```bash
export DEBUG=true
./deploy.sh
```

## 📝 Logging

### Local Logs
- Deployment process logged to console
- Timestamped entries
- Color-coded output (success, warning, error)

### Server Logs
- Server startup logs: `/path/to/ostg/ostg_server.log`
- System logs: `/var/log/messages` or `/var/log/syslog`

## 🔄 Workflow Examples

### Development Workflow
```bash
# Make code changes
vim utils/frr_docker.py

# Quick source deployment
./deploy_quick.sh
# Select option 2 (Source Code Only)
```

### Production Deployment
```bash
# Full deployment with backup
./deploy.sh --type full
```

### Emergency Fix
```bash
# Fast deployment without backup
./deploy_quick.sh
# Select option 6 (Deploy Without Backup)
```

### Package Update
```bash
# Force rebuild and deploy
./deploy.sh --type full --force-rebuild
```

## 🔐 Security Considerations

### Password Security
- Consider using SSH keys instead of passwords
- Use environment variables for sensitive data
- Restrict SSH access to specific IPs

### File Permissions
- Ensure deployment scripts are executable
- Set appropriate permissions on configuration files
- Use secure file transfer methods

### Backup Strategy
- Backups are created before deployment
- Backup directory includes timestamp
- Consider automated backup cleanup

## 📈 Performance Tips

### Fast Deployments
- Use `source-only` for code changes
- Use `wheel-only` for package updates
- Disable backup for development (`--no-backup`)
- Disable verification for trusted deployments (`--no-verify`)

### Network Optimization
- Use compression for large files
- Consider rsync for incremental updates
- Optimize SSH connection settings

## 🆘 Support

### Getting Help
```bash
# Show usage information
./deploy.sh --help

# Check script status
./deploy.sh --type full --no-start --no-verify
```

### Common Commands
```bash
# Check server status
ssh user@host "ps aux | grep ostg"

# View server logs
ssh user@host "tail -f /path/to/ostg/ostg_server.log"

# Test server connectivity
curl http://server:5051/health
```

---

**Note:** Always test deployments in a development environment before deploying to production.
