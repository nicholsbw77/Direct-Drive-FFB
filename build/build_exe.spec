# build/build_exe.spec
# Run with: pyinstaller build/build_exe.spec

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect data files to bundle
added_files = [
    ('config/*.json', 'config'),    # FFB profiles
    ('assets/*',      'assets'),    # icons, images
]

# Hidden imports that PyInstaller misses with dynamic imports
hidden_imports = [
    'pyirsdk',
    'comtypes',
    'comtypes.client',
    'pymodbus.client',
    'pymodbus.framer.rtu',
    'pkg_resources.py2_warn',
    'win32api',
    'win32con',
    'win32gui',
    'pywintypes',
]

a = Analysis(
    ['../plugin_main.py'],          # entry point
    pathex=['..'],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'PIL',
        'IPython',
        'tkinter',
        'unittest',
    ],
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
    name='DirtFFB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                           # compress with UPX if available
    console=False,                      # no console window (windowed app)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../assets/icon.ico',          # app icon
    version='build/version_info.txt',   # Windows version info
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DirtFFB',
)
