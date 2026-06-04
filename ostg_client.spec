# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Netgen GUI client — macOS .app bundle.
#
# Invoked by build_dmg.sh / build_macos_installer.sh. Version is read
# from pyproject.toml at build time so the .app's CFBundleVersion
# matches the wheel exactly — no more hardcoded 0.1.52 surprises.

import os
import re


def _parse_version():
    """Read `version = "..."` from pyproject.toml in repo root."""
    pyproject = os.path.join(SPECPATH, "pyproject.toml")
    try:
        with open(pyproject, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "0.0.0"


VERSION = _parse_version()
block_cipher = None


a = Analysis(
    ['run_tgen_client.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('widgets', 'widgets'),
        ('traffic_client', 'traffic_client'),
        ('utils', 'utils'),
        ('server', 'server'),
        # v0.3.16+: ship install_ostg_complete.py at the bundle root
        # so Help → Install/Upgrade Server → Fresh Install can find
        # it via _guess_installer_path()'s first candidate path
        # (<widgets>/../install_ostg_complete.py, which resolves to
        # <bundle>/install_ostg_complete.py inside a frozen
        # PyInstaller app). Without this, the Installer field stays
        # empty on every .dmg install — operator has to download the
        # file manually from the GitHub repo source, which is
        # undocumented and version-skew-prone.
        ('install_ostg_complete.py', '.'),
    ],
    excludes=['backup', 'backup.*', '*.backup.*', '*.tmp', '*.temp'],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'requests',
        'scapy',
        'scapy.contrib.lacp',
        'scapy.contrib.lldp',
        'scapy.contrib.igmp',
        'scapy.contrib.igmpv3',
        'scapy.contrib.pim',
        'scapy.layers.vrrp',
        'docker',
        'flask',
        'flask_cors',
        'cryptography',
        'paramiko',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Netgen Client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/add.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Netgen Client',
)

app = BUNDLE(
    coll,
    name='Netgen Client.app',
    icon='resources/icons/add.png',
    bundle_identifier='com.netgen.trafficgen.client',
    info_plist={
        'CFBundleName': 'Netgen Client',
        'CFBundleDisplayName': 'Netgen Traffic Generator Client',
        'CFBundleIdentifier': 'com.netgen.trafficgen.client',
        'CFBundleVersion': VERSION,
        'CFBundleShortVersionString': VERSION,
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleExecutable': 'Netgen Client',
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
        'NSRequiresAquaSystemAppearance': False,
    },
)
