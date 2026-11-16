@echo off
title 23zo AI Studio - Video, Image & AI Director
cd /d "%~dp0"

echo ========================================================
echo          23ZO AI STUDIO (VIDEO - IMAGE - CHAT)
echo ========================================================
echo.
echo [*] Dang khoi dong 23zo AI Studio tai http://localhost:7860 ...
echo     (Hay bat VPN Browser sang USA khi su dung)
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Khong tim thay moi truong ao .venv.
    pause
    exit /b
)

timeout /t 2 >nul
start "" http://localhost:7860
".venv\Scripts\python.exe" server.py
pause
