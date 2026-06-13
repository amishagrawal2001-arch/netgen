#!/bin/bash
#
# build_appimage.sh — Build a Linux AppImage for the Netgen GUI.
#
# AppImage is the "download a single .AppImage file, chmod +x, run"
# format that works on every modern distro (Ubuntu / Debian / RHEL /
# Rocky / Fedora / Arch / SUSE). No install, no root, no .deb / .rpm
# package-manager fuss. Best fit for the "ship to a customer who just
# wants the GUI" use case.
#
# Strategy:
#   * Build a regular PyInstaller one-folder distribution
#   * Wrap it in an AppDir structure with .desktop file + icon
#   * Use appimagetool to squash AppDir into a single AppImage
#
# Requirements on the build host:
#   * Linux (AppImage can only be built on Linux — the AppRun binary
#     and the squashfs tooling are Linux-native)
#   * Python 3.9+
#   * Internet access (downloads appimagetool on first run)
#   * fuse2 or fuse3 for testing the resulting AppImage locally
#
# Usage:
#   ./build_appimage.sh

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'
log()  { echo -e "${GREEN}[appimage] $1${NC}"; }
warn() { echo -e "${YELLOW}[appimage] WARNING: $1${NC}"; }
err()  { echo -e "${RED}[appimage] ERROR: $1${NC}" >&2; exit 1; }
info() { echo -e "${BLUE}[appimage] $1${NC}"; }

# ───────────────────────────────────────────────────────────────────
# Pre-flight
# ───────────────────────────────────────────────────────────────────

if [[ "$(uname -s)" != "Linux" ]]; then
    err "AppImage can only be built on Linux. Run this on a Linux box or use the GitHub Actions workflow."
fi

VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
[[ -z "$VERSION" ]] && err "Could not parse version from pyproject.toml"
log "Building AppImage for Netgen $VERSION"

# ───────────────────────────────────────────────────────────────────
# Build venv + dependencies
# ───────────────────────────────────────────────────────────────────

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[[ -z "$PYTHON_BIN" ]] && err "No python3 ≥ 3.9 on PATH."

BUILD_VENV=".build_appimage_venv"
if [[ ! -d "$BUILD_VENV" ]]; then
    log "Creating build venv: $BUILD_VENV"
    "$PYTHON_BIN" -m venv "$BUILD_VENV"
fi
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"

log "Installing build prerequisites..."
pip install --quiet --upgrade pip setuptools wheel
pip install --quiet pyinstaller pillow
pip install --quiet -r requirements.txt

# ───────────────────────────────────────────────────────────────────
# Build PyInstaller one-folder dist
# ───────────────────────────────────────────────────────────────────

log "Cleaning previous artifacts..."
rm -rf build/ AppDir/ "dist/Netgen Client" "dist/Netgen-Client-${VERSION}.AppImage" 2>/dev/null || true

# We reuse the macOS .spec but force the BUNDLE step to no-op on Linux
# by setting NO_BUNDLE in the env. Actually simpler: write a tiny
# one-off Linux spec inline so we don't have to surgery the macOS one.
APPIMAGE_SPEC=".build_appimage.spec"
cat > "$APPIMAGE_SPEC" <<PYEOF
# -*- mode: python ; coding: utf-8 -*-
import os, re
def _v():
    try:
        for ln in open(os.path.join(SPECPATH, "pyproject.toml")):
            m = re.match(r'^\s*version\s*=\s*"([^"]+)"', ln)
            if m: return m.group(1)
    except Exception: pass
    return "0.0.0"
VERSION = _v()
block_cipher = None
a = Analysis(
    ['run_tgen_client.py'],
    datas=[
        ('resources', 'resources'),
        ('widgets', 'widgets'),
        ('traffic_client', 'traffic_client'),
        ('utils', 'utils'),
        ('server', 'server'),
    ],
    hiddenimports=[
        'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'requests', 'scapy', 'docker', 'flask', 'flask_cors',
        'cryptography', 'paramiko',
        'scapy.contrib.lacp', 'scapy.contrib.lldp',
        'scapy.contrib.igmp', 'scapy.contrib.igmpv3',
        'scapy.contrib.pim', 'scapy.layers.vrrp',
    ],
    excludes=['backup', 'backup.*', '*.backup.*'],
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='netgen-client', console=False, upx=True,
          icon='resources/icons/netgen.png')
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
               strip=False, upx=True, name='netgen-client')
PYEOF

log "Running PyInstaller..."
pyinstaller --clean --noconfirm "$APPIMAGE_SPEC"

PYI_DIST="dist/netgen-client"
if [[ ! -d "$PYI_DIST" ]]; then
    err "PyInstaller didn't produce $PYI_DIST."
fi

# ───────────────────────────────────────────────────────────────────
# Build AppDir
# ───────────────────────────────────────────────────────────────────

log "Constructing AppDir..."
APPDIR="AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Move PyInstaller output into AppDir/usr/bin.
mv "$PYI_DIST"/* "$APPDIR/usr/bin/"

# Top-level AppRun — what the AppImage executes when launched.
# It just chains to the bundled binary, preserving argv.
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/netgen-client" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Top-level .desktop file (required by AppImage spec).
cat > "$APPDIR/netgen-client.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Netgen Client
GenericName=Network Traffic Generator
Comment=Netgen GUI client
Exec=netgen-client
Icon=netgen-client
Terminal=false
Categories=Network;System;
DESKTOP
# A copy under the canonical XDG path so DE menus see it post-install.
cp "$APPDIR/netgen-client.desktop" "$APPDIR/usr/share/applications/"

# Icon — AppImage spec requires both a top-level icon and one in
# usr/share/icons/hicolor/<size>/apps/. We use whatever PNG ships
# in resources/icons; if it's not 256x256 the AppImage still works
# but desktop integration might pick a fallback.
ICON_SRC=""
# v0.5.116: prefer netgen.png (the speedometer+packet app icon,
# 256×256 by default — generated by scripts/generate_app_icon.py).
# Older guesses kept as fallback for partially-rebuilt checkouts.
for guess in resources/icons/netgen.png resources/icons/ostg.png resources/icons/icon.png resources/icons/add.png; do
    if [[ -f "$guess" ]]; then
        ICON_SRC="$guess"
        break
    fi
done
if [[ -n "$ICON_SRC" ]]; then
    cp "$ICON_SRC" "$APPDIR/netgen-client.png"
    cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/netgen-client.png"
else
    warn "No icon found; AppImage will use a generic icon."
fi

# ───────────────────────────────────────────────────────────────────
# Fetch appimagetool + squash AppDir into AppImage
# ───────────────────────────────────────────────────────────────────

APPIMAGETOOL=".build_appimagetool"
if [[ ! -x "$APPIMAGETOOL" ]]; then
    log "Fetching appimagetool..."
    ARCH="x86_64"
    case "$(uname -m)" in
        x86_64) ARCH=x86_64 ;;
        aarch64|arm64) ARCH=aarch64 ;;
        i?86) ARCH=i686 ;;
    esac
    URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    if command -v curl &>/dev/null; then
        curl -L -o "$APPIMAGETOOL" "$URL"
    else
        wget -O "$APPIMAGETOOL" "$URL"
    fi
    chmod +x "$APPIMAGETOOL"
fi

log "Squashing AppDir into AppImage..."
OUT="dist/Netgen-Client-${VERSION}-linux-$(uname -m).AppImage"
# ARCH env tells appimagetool what to write into the AppImage header
# when running on something that's not the host arch (cross-builds).
ARCH=$(uname -m) "./${APPIMAGETOOL}" "$APPDIR" "$OUT"

if [[ ! -f "$OUT" ]]; then
    err "appimagetool didn't produce $OUT — check the output above."
fi
SIZE=$(du -h "$OUT" | cut -f1)

# ───────────────────────────────────────────────────────────────────
# Done
# ───────────────────────────────────────────────────────────────────

cat <<DONE

${GREEN}═══════════════════════════════════════════════════════════════════${NC}
${GREEN}  Linux AppImage build complete (Netgen $VERSION)${NC}
${GREEN}═══════════════════════════════════════════════════════════════════${NC}

  Artifact:  ${BLUE}$OUT${NC}
  Size:      ${BLUE}$SIZE${NC}

Test it locally:
  ${BLUE}chmod +x $OUT${NC}
  ${BLUE}./$OUT -s http://lab-box:5050${NC}

Ship it:
  Attach to a GitHub release alongside the .exe and .dmg.
  Customers download, ${BLUE}chmod +x${NC}, and run — no install, no root,
  no package manager. Works on every modern distro.

DONE
