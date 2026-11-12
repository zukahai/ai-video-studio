@echo off
title HaiAI Studio - Video, Image & AI Director
cd /d "%~dp0"

echo ========================================================
echo           HAIAI STUDIO (VIDEO - IMAGE - CHAT)
echo ========================================================
echo.
echo [!] LUU Y QUAN TRONG:
echo     Hay chac chan ban da BAT VPN sang My (US) tren may tinh
echo     truoc khi gui OTP hoac khoi tao Video / Anh.
echo.
echo [*] Dang khoi dong HaiAI Studio tai http://localhost:7860 ...
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
