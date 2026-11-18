@echo off
title 23zo AI Studio - Dang khoi dong...
cd /d "%~dp0"
set "BAT_PATH=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$code = (Get-Content -LiteralPath $env:BAT_PATH -Raw) -split '(?m)^::#PS_START#\r?\n', 2; Invoke-Expression $code[1]"
exit /b %errorlevel%

::#PS_START#
try { Clear-Host } catch {}

Write-Host '================================================================' -ForegroundColor Cyan
Write-Host '          23ZO AI STUDIO - CREATIVE AI DIRECTOR                 ' -ForegroundColor White -BackgroundColor DarkBlue
Write-Host '================================================================' -ForegroundColor Cyan

$ws = 'default'
if (Test-Path '.muse-state\active_workspace.txt') {
    $rawWs = (Get-Content '.muse-state\active_workspace.txt' -Raw).Trim()
    if ($rawWs) { $ws = $rawWs }
}

Write-Host ("  [*] Thu muc    : " + (Get-Location).Path) -ForegroundColor Gray
Write-Host ("  [*] Khong gian : " + $ws + " (outputs\" + $ws + ")") -ForegroundColor Yellow
Write-Host "  [*] Dia chi web: http://localhost:7860" -ForegroundColor Cyan
Write-Host ""

Write-Host "  [ 15%] [###-----------------] Kiem tra moi truong ao .venv..." -ForegroundColor DarkCyan
$pyw = ".venv\Scripts\pythonw.exe"
$py  = ".venv\Scripts\python.exe"
if (-not (Test-Path $pyw) -and -not (Test-Path $py)) {
    Write-Host "  [LOI] Khong tim thay .venv! Hay cai dat moi truong Python truoc." -ForegroundColor Red
    try { Read-Host "  Nhan Enter de thoat" } catch {}
    exit 1
}

Write-Host "  [ 30%] [######--------------] Kiem tra trang thai cong 7860..." -ForegroundColor DarkCyan
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 2
    if ($r.StatusCode -eq 200) {
        Write-Host "  [100%] [####################] May chu da san sang!" -ForegroundColor Green
        Write-Host "  [OK] Dang mo trinh duyet http://localhost:7860 ..." -ForegroundColor Green
        Start-Process 'http://localhost:7860'
        Start-Sleep -Milliseconds 900
        exit 0
    }
} catch {}

Write-Host "  [ 45%] [#########-----------] Dang khoi chay server.py chay ngam..." -ForegroundColor Magenta
if (Test-Path $pyw) {
    Start-Process -FilePath $pyw -ArgumentList "server.py" -WorkingDirectory (Get-Location).Path -WindowStyle Hidden
} else {
    Start-Process -FilePath $py -ArgumentList "server.py" -WorkingDirectory (Get-Location).Path -WindowStyle Minimized
}

$steps = @(
    'Nap thu vien FastAPI & MuseAI',
    'Ket noi kho du lieu & khong gian',
    'Khoi tao route Video / Movie / Chat',
    'Kiem tra san sang cong 7860'
)

for ($i = 1; $i -le 120; $i++) {
    $pct = [Math]::Min(95, 45 + [Math]::Floor($i * 0.5))
    $filled = [Math]::Floor($pct / 5)
    $bar = ('#' * $filled) + ('-' * (20 - $filled))
    $msg = $steps[$i % $steps.Count]
    Write-Host ("`r  [{0,3}%] [{1}] {2}          " -f $pct, $bar, $msg) -NoNewline -ForegroundColor Yellow

    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:7860/api/auth/status' -TimeoutSec 1
        if ($r.StatusCode -eq 200) {
            Write-Host "`r  [100%] [####################] Da san sang! Dang mo trinh duyet...          " -ForegroundColor Green
            Start-Process 'http://localhost:7860'
            Start-Sleep -Milliseconds 1000
            exit 0
        }
    } catch {}

    Start-Sleep -Milliseconds 250
}

Write-Host ""
Write-Host "  [CANH BAO] Het thoi gian cho server (cong 7860 khong phan hoi)." -ForegroundColor Red
try { Read-Host "  Nhan Enter de thoat" } catch {}
exit 1
