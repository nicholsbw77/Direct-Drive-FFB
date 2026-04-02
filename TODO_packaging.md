# TODO: Windows Executable Packaging

## Goal

Produce a single Windows installer (`DirtFFB_Setup_v1.0.exe`) that:
1. Installs the app to `C:\Program Files\DirtFFB\`
2. Creates a Start Menu shortcut
3. Creates a Desktop shortcut (optional, user choice during install)
4. Includes an Uninstaller
5. Runs on Windows 10 and Windows 11 with **no Python required**

---

## Step 1: Prepare requirements.txt for Packaging

Update `requirements.txt` to include all packaging dependencies:

```
# Core runtime
pyirsdk>=1.3.0
pymodbus>=3.5.0
PyQt5>=5.15.0
numpy>=1.24.0

# Consumer wheel drivers
comtypes>=1.4.1
pywin32>=306

# Packaging tools (dev only, not bundled)
pyinstaller>=6.3.0
```

Create a separate `requirements-dev.txt` for packaging tools so end users
who clone the repo don't install PyInstaller unnecessarily.

---

## Step 2: Create `build/build_exe.spec` — PyInstaller Spec File

PyInstaller spec file gives full control over what gets bundled.
Create `build/build_exe.spec`:

```python
# build/build_exe.spec
# Run with: pyinstaller build/build_exe.spec

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect pyirsdk and other data files
added_files = [
    ('config/*.json', 'config'),          # FFB profiles
    ('assets/*', 'assets'),               # icons, images
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
    ['../plugin_main.py'],            # entry point
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
    upx=True,                          # compress with UPX if available
    console=False,                     # no console window (windowed app)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../assets/icon.ico',         # app icon
    version='build/version_info.txt',  # Windows version info
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
```

### Notes on PyInstaller
- Use `COLLECT` (not `--onefile`) for faster startup — onefile extracts to temp dir each run
- The output folder `dist/DirtFFB/` becomes the install directory
- `console=False` hides the command window
- `icon=` must be a multi-resolution `.ico` file

---

## Step 3: Create `build/version_info.txt` — Windows Version Metadata

```python
# build/version_info.txt
# Windows PE version info — shows in file Properties dialog

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'DirtFFB Project'),
        StringStruct('FileDescription', 'Dirt FFB Plugin for iRacing'),
        StringStruct('FileVersion', '1.0.0.0'),
        StringStruct('InternalName', 'DirtFFB'),
        StringStruct('LegalCopyright', ''),
        StringStruct('OriginalFilename', 'DirtFFB.exe'),
        StringStruct('ProductName', 'Dirt FFB Plugin'),
        StringStruct('ProductVersion', '1.0.0'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
```

---

## Step 4: Create `build/build_exe.bat` — One-Click Build Script

```batch
@echo off
REM build/build_exe.bat
REM Run this from the repo root to build the Windows executable

echo === Dirt FFB Plugin - Build Script ===
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

REM Install/update dependencies
echo Installing dependencies...
pip install -r requirements.txt -q
pip install pyinstaller -q

REM Clean previous build
echo Cleaning previous build...
if exist dist\DirtFFB rmdir /s /q dist\DirtFFB
if exist build\__pycache__ rmdir /s /q build\__pycache__

REM Run PyInstaller
echo Building executable...
pyinstaller build\build_exe.spec --distpath dist --workpath build\pyinstaller_work

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check errors above.
    pause
    exit /b 1
)

echo.
echo === Build Complete ===
echo Output: dist\DirtFFB\DirtFFB.exe
echo.

REM Optional: compile Inno Setup installer
where iscc >nul 2>&1
if not errorlevel 1 (
    echo Compiling installer...
    iscc build\installer\setup.iss
    echo Installer: dist\DirtFFB_Setup_v1.0.exe
) else (
    echo NOTE: Inno Setup not found. Skipping installer compilation.
    echo       Install Inno Setup from https://jrsoftware.org/isinfo.php
)

pause
```

---

## Step 5: Create `build/installer/setup.iss` — Inno Setup Installer Script

Inno Setup is free and produces a professional Windows installer.
Download from: https://jrsoftware.org/isinfo.php

```pascal
; build/installer/setup.iss
; Compile with: iscc setup.iss

#define MyAppName "Dirt FFB Plugin"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "DirtFFB Project"
#define MyAppExeName "DirtFFB.exe"
#define MyAppDir "..\..\dist\DirtFFB"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\DirtFFB
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\..\dist
OutputBaseFilename=DirtFFB_Setup_v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\DirtFFB.exe
PrivilegesRequired=lowest          ; no admin required
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0                    ; Windows 10 minimum

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Include all files from the PyInstaller output directory
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove settings on uninstall (optional — comment out to preserve user settings)
; Type: filesandordirs; Name: "{userappdata}\DirtFFB"
```

---

## Step 6: Handle Runtime Path for Bundled Resources

When running from PyInstaller exe, `__file__` paths don't work.
The app needs to find `config/*.json` and `assets/*` relative to the exe.

Create `src/resource_path.py`:
```python
"""
Resolve paths to bundled resources.

PyInstaller extracts files to a temp dir (_MEIPASS) when running as exe.
This helper finds the correct path whether running from source or as exe.
"""
import sys
import os
from pathlib import Path

def resource_path(relative_path: str) -> Path:
    """Get absolute path to a resource, works in dev and PyInstaller exe."""
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        base = Path(sys._MEIPASS)
    else:
        # Running from source
        base = Path(__file__).parent.parent  # repo root
    return base / relative_path
```

**Update these files to use `resource_path()`:**
- `src/config.py` — `CONFIG_DIR` and `DEFAULT_PROFILE` paths
- `src/gui/plugin_window.py` — `assets/setup_guide.png` path
- `plugin_main.py` — any hardcoded paths

---

## Step 7: Suppress Console Window on Error

When a windowed exe crashes, errors are swallowed. Add a global exception
handler that shows a QMessageBox before crashing:

In `plugin_main.py`:
```python
import sys
import traceback
from PyQt5.QtWidgets import QApplication, QMessageBox

def excepthook(exc_type, exc_value, exc_tb):
    """Show crash dialog instead of silently dying."""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Critical)
    msg.setWindowTitle("Dirt FFB Plugin — Error")
    msg.setText("An unexpected error occurred:")
    msg.setDetailedText(tb)
    msg.exec_()
    sys.exit(1)

sys.excepthook = excepthook
```

---

## Step 8: Create `assets/icon.ico`

The app needs a Windows `.ico` file for:
- The exe file icon
- The taskbar/tray icon
- The installer

Requirements:
- Multi-resolution ICO: 16x16, 32x32, 48x48, 64x64, 128x128, 256x256
- Simple design readable at 16x16 (consider a steering wheel silhouette or "D" icon)
- Can be created with free tools: IcoFX, GIMP, or online converter

Placeholder: copy any `.ico` file to `assets/icon.ico` to unblock the build.
The build will fail without it.

---

## Build Output Structure

After running `build_exe.bat`, the output is:
```
dist/
├── DirtFFB/                        <- PyInstaller output (the "install files")
│   ├── DirtFFB.exe                 <- main executable
│   ├── PyQt5/                      <- Qt DLLs
│   ├── numpy/                      <- numpy
│   ├── config/
│   │   ├── default_profile.json
│   │   └── dirt_sprint_car.json
│   └── assets/
│       ├── icon.ico
│       └── setup_guide.png
└── DirtFFB_Setup_v1.0.exe          <- Inno Setup installer (single file to distribute)
```

---

## Testing the Exe

Before distributing, test on a **clean Windows 10/11 VM** (no Python installed):
1. Run `DirtFFB_Setup_v1.0.exe` — should install without errors
2. Launch from Start Menu
3. Setup Wizard appears
4. Connect a wheel — should detect it
5. Open iRacing — app should connect
6. Join a dirt track session — FFB should activate
7. Close the app — should go to tray (not close)
8. Right-click tray → Exit — should shut down cleanly
9. Uninstall via Control Panel — should remove cleanly

---

## Files to Create

| File | Description |
|------|-------------|
| `build/build_exe.spec` | PyInstaller spec |
| `build/version_info.txt` | Windows PE version metadata |
| `build/build_exe.bat` | One-click build + package script |
| `build/installer/setup.iss` | Inno Setup installer script |
| `src/resource_path.py` | Path resolver for bundled resources |
| `assets/icon.ico` | Application icon (multi-resolution) |
| `assets/setup_guide.png` | Screenshot: iRacing FFB=0 setting |
