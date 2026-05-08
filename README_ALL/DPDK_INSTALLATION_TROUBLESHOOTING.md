# DPDK Installation Troubleshooting Guide

## Network Timeout Issues

If you encounter connection timeout errors when installing DPDK dependencies:

```
E: Failed to fetch http://us.archive.ubuntu.com/ubuntu/dists/jammy-updates/Contents-amd64
   Connection timed out [IP: 91.189.91.81 80]
```

### Solution 1: Use a Different Ubuntu Mirror (Recommended)

1. **Backup current sources.list:**
   ```bash
   sudo cp /etc/apt/sources.list /etc/apt/sources.list.backup
   ```

2. **Edit sources.list to use a different mirror:**
   ```bash
   sudo sed -i 's|http://us.archive.ubuntu.com|http://archive.ubuntu.com|g' /etc/apt/sources.list
   # Or use a specific mirror:
   # sudo sed -i 's|http://us.archive.ubuntu.com|http://mirror.ubuntu.com|g' /etc/apt/sources.list
   ```

3. **Try installation again:**
   ```bash
   cd /opt/OSTG/resources/dpdk
   ./dpdk_tx_worker.sh --install-deps --rewrite-src
   ```

### Solution 2: Skip Contents Files (Quick Fix)

The Contents files are optional metadata. You can ignore these errors:

```bash
# The script has been updated to handle this automatically
# But if you still see errors, you can manually install:

sudo apt-get update -y 2>&1 | grep -v "Contents.*Connection timed out" || true
sudo apt-get install -y build-essential meson ninja-build pkg-config \
                        libnuma-dev libelf-dev libpcap-dev
```

### Solution 3: Manual Installation (If Network Issues Persist)

If network issues continue, install dependencies manually:

```bash
# Update package lists (ignore Contents errors)
sudo apt-get update -y 2>&1 | grep -v "Contents" || true

# Install build dependencies
sudo apt-get install -y build-essential
sudo apt-get install -y meson ninja-build
sudo apt-get install -y pkg-config
sudo apt-get install -y libnuma-dev libelf-dev libpcap-dev

# Verify installation
meson --version
ninja --version
pkg-config --version
```

### Solution 4: Use Local Package Cache

If you have access to another Ubuntu system:

1. **On the working system:**
   ```bash
   apt-get download build-essential meson ninja-build pkg-config \
                    libnuma-dev libelf-dev libpcap-dev
   ```

2. **Transfer packages to the server:**
   ```bash
   scp *.deb user@server:/tmp/
   ```

3. **Install from local files:**
   ```bash
   sudo dpkg -i /tmp/*.deb
   sudo apt-get install -f  # Fix any dependencies
   ```

### Solution 5: Configure APT Timeouts

Add timeout configuration to `/etc/apt/apt.conf.d/99timeout`:

```bash
sudo tee /etc/apt/apt.conf.d/99timeout <<EOF
Acquire::http::Timeout "60";
Acquire::ftp::Timeout "60";
Acquire::Retries "3";
EOF
```

Then retry:
```bash
sudo apt-get update
```

## Updated Script Behavior

The DPDK installation scripts have been updated to:
- **Retry failed updates** up to 3 times
- **Ignore Contents file errors** (they're optional metadata)
- **Use longer timeouts** (30 seconds)
- **Continue installation** even if some updates fail

## Verification

After installation, verify dependencies:

```bash
# Check build tools
meson --version      # Should show version (0.60+)
ninja --version      # Should show version (1.10+)
pkg-config --version # Should show version

# Check libraries
pkg-config --exists libnuma && echo "libnuma: OK" || echo "libnuma: Missing"
pkg-config --exists libelf && echo "libelf: OK" || echo "libelf: Missing"
pkg-config --exists libpcap && echo "libpcap: OK" || echo "libpcap: Missing"
```

## Common Issues

### Issue: "meson not found"
**Solution:** Install meson:
```bash
sudo apt-get install -y meson
# Or via pip:
pip3 install meson
```

### Issue: "ninja not found"
**Solution:** Install ninja:
```bash
sudo apt-get install -y ninja-build
```

### Issue: "pkg-config not found"
**Solution:** Install pkg-config:
```bash
sudo apt-get install -y pkg-config
```

### Issue: "libnuma-dev not found"
**Solution:** Install development package:
```bash
sudo apt-get install -y libnuma-dev
```

## Network Configuration

If network issues persist, check:

1. **DNS resolution:**
   ```bash
   ping -c 3 archive.ubuntu.com
   ```

2. **Firewall rules:**
   ```bash
   sudo iptables -L -n | grep -i drop
   ```

3. **Proxy settings:**
   ```bash
   echo $http_proxy
   echo $https_proxy
   ```

4. **Repository accessibility:**
   ```bash
   curl -I http://archive.ubuntu.com/ubuntu/dists/jammy/Release
   ```

## Alternative: Build DPDK Without Installing Dependencies

If you can't install dependencies via apt, you can:

1. **Build DPDK manually** (if you have DPDK source):
   ```bash
   cd ~/dpdk
   meson setup build
   ninja -C build
   ```

2. **Use pre-built DPDK** (if available):
   ```bash
   export DPDK_TREE=/path/to/dpdk/build
   cd /opt/OSTG/resources/dpdk
   ./dpdk_tx_worker.sh --dpdk-tree $DPDK_TREE --rewrite-src
   ```

## Getting Help

If issues persist:
1. Check server network connectivity
2. Verify Ubuntu repository mirrors are accessible
3. Check system logs: `journalctl -xe`
4. Try installation during off-peak hours
5. Contact network administrator if behind corporate firewall




