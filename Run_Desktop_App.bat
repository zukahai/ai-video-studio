@echo off
title 23zo AI Studio - Desktop App
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Khong tim thay moi truong ao .venv.
    pause
    exit /b 1
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" desktop_app.py
) else (
    ".venv\Scripts\python.exe" desktop_app.py
)
