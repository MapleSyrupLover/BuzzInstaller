@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    pause
    exit /b 1
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw tools\dev_gui.py
    exit /b 0
)

python tools\dev_gui.py
if errorlevel 1 pause
