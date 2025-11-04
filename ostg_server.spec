# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['run_tgen_server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('utils', 'utils'),
        ('ostg', 'ostg'),
        ('ostg_docker', 'ostg_docker'),
        ('systemd', 'systemd'),
        ('frr.conf.template', '.'),
        ('start-frr.sh', '.'),
        ('Dockerfile.frr', '.'),
    ],
    excludes=['backup', 'backup.*', '*.backup.*', '*.tmp', '*.temp'],
    hiddenimports=[
        'flask',
        'requests',
        'docker',
        'scapy',
        'json',
        'logging',
        'ipaddress',
        'uuid',
        'subprocess',
        'threading',
        'time',
        'os',
        'sys',
        'socket',
        'multiprocessing',
        'concurrent.futures',
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
    name='OSTG Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Server needs console for logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/start.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OSTG Server',
)

app = BUNDLE(
    coll,
    name='OSTG Server.app',
    icon='resources/icons/start.png',
    bundle_identifier='com.ostg.trafficgen.server',
    info_plist={
        'CFBundleName': 'OSTG Server',
        'CFBundleDisplayName': 'OSTG Traffic Generator Server',
        'CFBundleIdentifier': 'com.ostg.trafficgen.server',
        'CFBundleVersion': '0.1.52',
        'CFBundleShortVersionString': '0.1.52',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleExecutable': 'OSTG Server',
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'OSTG Configuration',
                'CFBundleTypeExtensions': ['ostg'],
                'CFBundleTypeRole': 'Editor',
            }
        ],
    },
)

# Override dist directory for macOS builds
import os
if os.environ.get('MACOS_BUILD'):
    coll.dist = 'dist_macos'
    app.dist = 'dist_macos'
