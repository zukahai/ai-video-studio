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

start "" /b powershell -NoProfile -Command "for ($i=0; $i -lt 80; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process 'http://localhost:7860'; break } } catch {}; Start-Sleep -Milliseconds 250 }"
".venv\Scripts\python.exe" server.py
pause
