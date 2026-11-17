@echo off
title 23zo AI Studio - Video, Image & AI Director
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    if not exist ".venv\Scripts\python.exe" (
        echo [LOI] Khong tim thay moi truong ao .venv.
        pause
        exit /b 1
    )
)

powershell -NoProfile -WindowStyle Hidden -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if %errorlevel%==0 (
    start "" http://localhost:7860
    exit /b 0
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" server.py
) else (
    start /min "" ".venv\Scripts\python.exe" server.py
)

powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 80; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process 'http://localhost:7860'; break } } catch {}; Start-Sleep -Milliseconds 250 }"
exit /b 0
