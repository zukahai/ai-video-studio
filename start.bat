@echo off
title 23zo AI Studio - Dang khoi dong...
cd /d "%~dp0"

powershell -NoProfile -Command ^
  "Clear-Host; " ^
  "Write-Host '================================================================' -ForegroundColor Cyan; " ^
  "Write-Host '          23ZO AI STUDIO - CREATIVE AI DIRECTOR                 ' -ForegroundColor White -BackgroundColor DarkBlue; " ^
  "Write-Host '================================================================' -ForegroundColor Cyan; " ^
  "$ws = 'default'; if (Test-Path '.muse-state\active_workspace.txt') { $ws = (Get-Content '.muse-state\active_workspace.txt' -Raw).Trim() }; " ^
  "Write-Host ('  [*] Thu muc    : ' + (Get-Location).Path) -ForegroundColor Gray; " ^
  "Write-Host ('  [*] Khong gian : ' + $ws + ' (outputs\\' + $ws + ')') -ForegroundColor Yellow; " ^
  "Write-Host ('  [*] Dia chi web: http://localhost:7860') -ForegroundColor Cyan; " ^
  "Write-Host ''; " ^
  "Write-Host '  [15%] Kiem tra moi truong ao .venv...' -ForegroundColor DarkCyan"

if not exist ".venv\Scripts\pythonw.exe" (
    if not exist ".venv\Scripts\python.exe" (
        powershell -NoProfile -Command "Write-Host '  [LOI] Khong tim thay .venv!' -ForegroundColor Red"
        pause
        exit /b 1
    )
)

powershell -NoProfile -Command ^
  "Write-Host '  [30%] Kiem tra trang thai cong 7860...' -ForegroundColor DarkCyan; " ^
  "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if %errorlevel%==0 (
    powershell -NoProfile -Command ^
      "Write-Host '  [100%] [####################] May chu da san sang!' -ForegroundColor Green; " ^
      "Write-Host '  [OK] Dang mo trinh duyet http://localhost:7860 ...' -ForegroundColor Green; " ^
      "Start-Process 'http://localhost:7860'; Start-Sleep -Milliseconds 900"
    exit /b 0
)

powershell -NoProfile -Command "Write-Host '  [45%] Dang khoi chay server.py chay ngam (pythonw.exe)...' -ForegroundColor Magenta"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" server.py
) else (
    start /min "" ".venv\Scripts\python.exe" server.py
)

powershell -NoProfile -Command ^
  "$steps = @('Nap thu vien FastAPI & MuseAI', 'Ket noi kho du lieu & khong gian', 'Khoi tao route Video / Movie / Chat', 'Kiem tra san sang cong 7860'); " ^
  "for ($i = 1; $i -le 100; $i++) { " ^
  "  $pct = [Math]::Min(95, 45 + ($i * 2)); " ^
  "  $filled = [Math]::Floor($pct / 5); " ^
  "  $bar = ('#' * $filled) + ('-' * (20 - $filled)); " ^
  "  $msg = $steps[$i % $steps.Count]; " ^
  "  Write-Host (\"`r  [{0,3}%] [{1}] {2}   \" -f $pct, $bar, $msg) -NoNewline -ForegroundColor Yellow; " ^
  "  try { " ^
  "    $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1; " ^
  "    if ($r.StatusCode -eq 200) { " ^
  "      Write-Host (\"`r  [100%] [####################] Da san sang! Dang mo trinh duyet...      \") -ForegroundColor Green; " ^
  "      Start-Process 'http://localhost:7860'; " ^
  "      Start-Sleep -Milliseconds 1100; " ^
  "      exit 0 " ^
  "    } " ^
  "  } catch {}; " ^
  "  Start-Sleep -Milliseconds 250 " ^
  "} " ^
  "Write-Host ''; Write-Host '  [CANH BAO] Het thoi gian cho server.' -ForegroundColor Red; Start-Sleep -Seconds 2"
exit /b 0
