@echo off
REM build/build_exe.bat
REM Run this script from the repo root to build the Windows executable.
REM
REM Requirements:
REM   - Python 3.11+ on PATH
REM   - Optional: Inno Setup (iscc) on PATH for installer compilation
REM     Download from: https://jrsoftware.org/isinfo.php

echo === Dirt FFB Plugin - Build Script ===
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

REM Install / update runtime dependencies
echo Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo ERROR: Failed to install requirements.
    pause
    exit /b 1
)

REM Install PyInstaller (dev tool, not bundled in requirements.txt)
pip install pyinstaller -q
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller.
    pause
    exit /b 1
)

REM Clean previous build artefacts
echo Cleaning previous build...
if exist dist\DirtFFB           rmdir /s /q dist\DirtFFB
if exist build\pyinstaller_work rmdir /s /q build\pyinstaller_work
if exist build\__pycache__      rmdir /s /q build\__pycache__

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
    if errorlevel 1 (
        echo WARNING: Inno Setup compilation failed.
    ) else (
        echo Installer: dist\DirtFFB_Setup_v1.0.exe
    )
) else (
    echo NOTE: Inno Setup not found. Skipping installer compilation.
    echo       Install Inno Setup from https://jrsoftware.org/isinfo.php
    echo       then re-run this script to also produce the installer.
)

echo.
pause
