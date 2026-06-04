# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Netgen GUI client — Windows .exe.
#
# Invoked by build_windows.ps1. Different from ostg_client.spec
# because:
#   * No BUNDLE block (BUNDLE is macOS-only)
#   * console=False keeps cmd.exe from opening behind the GUI
#   * Icon is .ico (Windows shell ignores .png in Explorer)
#   * version_info gets the Windows file-properties tab
#
# Build target: a Netgen-Client-<version>-windows folder + a single
# Netgen-Client-<version>-windows.exe one-file build. The folder is
# faster to launch (no extract step); the one-file is what we ship
# in the GitHub release.

import os
import re


def _parse_version():
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
# PyInstaller version-info needs a 4-tuple, default the 4th to 0.
VTUP = tuple([int(p) for p in (VERSION.split(".") + ["0", "0", "0"])[:4]])
ONEFILE = bool(os.environ.get("NETGEN_ONEFILE", "1"))

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
        # empty on every .exe install — operator has to download the
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


# Windows file-properties metadata. Right-clicking the .exe in
# Explorer → Properties → Details shows these fields. Helps customers
# verify which version they installed.
version_info = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VTUP},
    prodvers={VTUP},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName',      'Netgen'),
        StringStruct('FileDescription',  'Netgen Traffic Generator Client'),
        StringStruct('FileVersion',      '{VERSION}'),
        StringStruct('InternalName',     'NetgenClient'),
        StringStruct('OriginalFilename', 'Netgen-Client.exe'),
        StringStruct('ProductName',      'Netgen'),
        StringStruct('ProductVersion',   '{VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

# Write the version-info to a tempfile that PyInstaller can read.
import tempfile
_ver_file = os.path.join(tempfile.gettempdir(), "netgen_client_version_info.txt")
with open(_ver_file, "w") as fh:
    fh.write(version_info)


_icon = None
for guess in ("resources/icons/ostg.ico", "resources/icons/icon.ico", "resources/icons/add.png"):
    if os.path.isfile(os.path.join(SPECPATH, guess)):
        _icon = guess
        break


if ONEFILE:
    # Single-file .exe — what we attach to GitHub releases. Slower to
    # start (extracts to temp) but ships as one file.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=f'Netgen-Client-{VERSION}-windows',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,                # no cmd.exe window behind the GUI
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=_icon,
        version=_ver_file,
    )
else:
    # Folder build — faster startup, ships as a directory tree.
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
        icon=_icon,
        version=_ver_file,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=f'Netgen-Client-{VERSION}-windows',
    )
