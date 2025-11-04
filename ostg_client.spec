# -*- mode: python ; coding: utf-8 -*-

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
    ],
    excludes=['backup', 'backup.*', '*.backup.*', '*.tmp', '*.temp'],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui', 
        'PyQt5.QtWidgets',
        'requests',
        'scapy',
        'docker',
        'flask',
        'json',
        'logging',
        'ipaddress',
        'uuid',
        'subprocess',
        'threading',
        'time',
        'os',
        'sys',
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
    name='OSTG Client',
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
    name='OSTG Client',
)

app = BUNDLE(
    coll,
    name='OSTG Client.app',
    icon='resources/icons/add.png',
    bundle_identifier='com.ostg.trafficgen.client',
    info_plist={
        'CFBundleName': 'OSTG Client',
        'CFBundleDisplayName': 'OSTG Traffic Generator Client',
        'CFBundleIdentifier': 'com.ostg.trafficgen.client',
        'CFBundleVersion': '0.1.52',
        'CFBundleShortVersionString': '0.1.52',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleExecutable': 'OSTG Client',
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
        'NSRequiresAquaSystemAppearance': False,
    },
)

# Override dist directory for macOS builds
import os
if os.environ.get('MACOS_BUILD'):
    coll.dist = 'dist_macos'
    app.dist = 'dist_macos'
