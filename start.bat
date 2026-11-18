@echo off
title 23zo AI Studio - Dang khoi dong...
cd /d "%~dp0"

echo ========================================================
echo          23ZO AI STUDIO (VIDEO - IMAGE - CHAT)
echo ========================================================
echo.
echo [1/3] Kiem tra moi truong ao .venv...

if not exist ".venv\Scripts\pythonw.exe" (
    if not exist ".venv\Scripts\python.exe" (
        echo [LOI] Khong tim thay moi truong ao .venv.
        pause
        exit /b 1
    )
)

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if %errorlevel%==0 (
    echo [OK] May chu da san sang! Dang mo http://localhost:7860 ...
    start "" http://localhost:7860
    timeout /t 1 >nul
    exit /b 0
)

echo [2/3] Dang khoi dong may chu 23zo AI Studio tren cong 7860...
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" server.py
) else (
    start /min "" ".venv\Scripts\python.exe" server.py
)

echo [3/3] Dang cho web khoi dong hoan tat...
powershell -NoProfile -Command "for ($i=0; $i -lt 120; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { Write-Host '[OK] Web da san sang! Dang mo trinh duyet...'; Start-Process 'http://localhost:7860'; break } } catch {}; Write-Host -NoNewline '.'; Start-Sleep -Milliseconds 300 }"
echo.
timeout /t 1 >nul
exit /b 0
