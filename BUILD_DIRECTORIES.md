# OSTG Build Directory Structure

This document explains the different build directories used by various OSTG build scripts to avoid conflicts.

## 📁 Directory Usage

### **`dist/` - Python Wheel Builds**
- **Used by:** `rebuild_wheel.sh`, `rebuild_quick.sh`
- **Contains:** Python wheel packages (`.whl` files)
- **Purpose:** Standard Python package distribution

### **`dist_macos/` - macOS App Builds**
- **Used by:** `build_dmg_quick.sh`, `build_macos_installer.sh`
- **Contains:** macOS application bundles (`.app` files)
- **Purpose:** Native macOS applications

### **`build/` - Temporary Build Files**
- **Used by:** All build scripts
- **Contains:** Temporary build artifacts
- **Purpose:** PyInstaller and Python build cache

### **`macos_build/` - Complete Installer**
- **Used by:** `build_macos_installer.sh` only
- **Contains:** Complete installer package before DMG creation
- **Purpose:** Full distribution package assembly

## 🔧 Script Conflicts Resolved

### **Before (Conflicting):**
```bash
./rebuild_wheel.sh      # Uses dist/
./build_dmg.sh    # Uses dist/ (OVERWRITES wheel files!)
```

### **After (Separated):**
```bash
./rebuild_wheel.sh      # Uses dist/ (wheel files)
./build_dmg.sh    # Uses dist_macos/ (app files)
```

## 📋 Build Script Directory Usage

| Script | Uses | Output |
|--------|------|--------|
| `rebuild_wheel.sh` | `dist/` | `*.whl` files |
| `rebuild_quick.sh` | `dist/` | `*.whl` files |
| `build_dmg.sh` | `dist_macos/` | `*.dmg` files |
| `build_macos_installer.sh` | `dist/`, `dist_macos/`, `macos_build/` | `*.dmg` files |

## 🚀 Usage Examples

### **Build Python Wheel:**
```bash
./rebuild_wheel.sh
# Creates: dist/ostg_trafficgen-0.1.52-py3-none-any.whl
```

### **Build macOS Apps:**
```bash
./build_dmg.sh
# Creates: dist_macos/OSTG Client.app, dist_macos/OSTG Server.app
# Output: OSTG-TrafficGenerator-0.1.52-Quick.dmg
```

### **Build Complete Installer:**
```bash
./build_macos_installer.sh
# Creates: dist/*.whl, dist_macos/*.app, macos_build/*, *.dmg
# Output: OSTG-TrafficGenerator-0.1.52-macOS.dmg
```

## ✅ Benefits

1. **No Conflicts** - Each script uses its own directories
2. **Parallel Builds** - Can run multiple scripts simultaneously
3. **Clean Separation** - Wheel builds don't interfere with app builds
4. **Preserved Artifacts** - Previous builds aren't accidentally overwritten

## 🧹 Cleanup

To clean all build directories:
```bash
rm -rf dist/ dist_macos/ build/ macos_build/ *.egg-info/
```

Or use individual script cleanup options when available.
