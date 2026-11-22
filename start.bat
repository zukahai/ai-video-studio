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

try {
    $programsDir = [Environment]::GetFolderPath('Programs')
    $lnkPath = Join-Path $programsDir "23zo AI Studio.lnk"
    $targetBat = Join-Path (Get-Location).Path "start.bat"
    $iconPath = Join-Path (Get-Location).Path "static\logo.ico"
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnkPath)
    if ($sc.TargetPath -ne $targetBat -or $sc.WorkingDirectory -ne (Get-Location).Path) {
        $sc.TargetPath = $targetBat
        $sc.WorkingDirectory = (Get-Location).Path
        $sc.Description = "Khoi dong 23zo AI Studio"
        if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
        $sc.Save()
        Write-Host "  [OK] Da tao shortcut trong Windows Search: '23zo AI Studio'" -ForegroundColor Green
    }
} catch {}

function Find-SystemPython {
    $candidates = @()
    foreach ($cmdName in @('python', 'python3')) {
        $cmd = Get-Command $cmdName -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and ($cmd.Source -notmatch 'WindowsApps')) {
            $candidates += $cmd.Source
        }
    }
    $searchRoots = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "C:\Program Files\Python3*\python.exe"
    )
    foreach ($pattern in $searchRoots) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
        foreach ($f in $found) { $candidates += $f.FullName }
    }
    foreach ($exe in ($candidates | Select-Object -Unique)) {
        try {
            $ver = & $exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) {
                return $exe
            }
        } catch {}
    }
    return $null
}

Write-Host "  [ 10%] [##------------------] Kiem tra moi truong ao .venv..." -ForegroundColor DarkCyan
$pyw = ".venv\Scripts\pythonw.exe"
$py  = ".venv\Scripts\python.exe"

if (-not (Test-Path $pyw) -and -not (Test-Path $py)) {
    Write-Host "  [!] Chua co .venv - Bat dau tu dong cai dat moi truong tu dau..." -ForegroundColor Yellow
    $sysPy = Find-SystemPython

    if (-not $sysPy) {
        Write-Host "  [ 15%] [###-----------------] May chua co Python 3.10+. Dang tu dong tai & cai dat..." -ForegroundColor Magenta
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "         -> Dang cai Python 3.12 qua winget (vui long cho)..." -ForegroundColor Gray
            & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements --scope user | Out-Null
            $sysPy = Find-SystemPython
        }
        if (-not $sysPy) {
            $pyUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
            $installer = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
            Write-Host "         -> Dang tai bo cai Python 3.12.8 tu python.org..." -ForegroundColor Gray
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -UseBasicParsing -Uri $pyUrl -OutFile $installer -TimeoutSec 120
                Write-Host "         -> Dang cai dat Python 3.12.8 (che do tu dong)..." -ForegroundColor Gray
                $proc = Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait -PassThru
                Remove-Item $installer -Force -ErrorAction SilentlyContinue
                $sysPy = Find-SystemPython
            } catch {
                Write-Host ("  [LOI] Khong tai duoc Python: " + $_.Exception.Message) -ForegroundColor Red
            }
        }
        if (-not $sysPy) {
            Write-Host "  [LOI] Khong the tu dong cai Python. Hay kiem tra ket noi mang hoac cai Python 3.11+ thu cong." -ForegroundColor Red
            try { Read-Host "  Nhan Enter de thoat" } catch {}
            exit 1
        }
        Write-Host ("  [OK] Da cai dat Python: " + $sysPy) -ForegroundColor Green
    } else {
        Write-Host ("  [ 15%] [###-----------------] Tim thay Python: " + $sysPy) -ForegroundColor DarkCyan
    }

    Write-Host "  [ 20%] [####----------------] Dang tao moi truong ao .venv..." -ForegroundColor Cyan
    & $sysPy -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) {
        Write-Host "  [LOI] Tao moi truong ao .venv that bai!" -ForegroundColor Red
        try { Read-Host "  Nhan Enter de thoat" } catch {}
        exit 1
    }

    Write-Host "  [ 25%] [#####---------------] Dang cai dat thu vien trong requirements.txt (chi 1 lan dau)..." -ForegroundColor Cyan
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [LOI] Cai dat thu vien pip that bai! Kiem tra ket noi mang va thu lai." -ForegroundColor Red
        try { Read-Host "  Nhan Enter de thoat" } catch {}
        exit 1
    }
    if (Test-Path 'requirements.txt') {
        (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash | Set-Content '.venv\.req_hash' -NoNewline
    }
    Write-Host "  [OK] Cai dat moi truong hoan tat!" -ForegroundColor Green
} else {
    $reqHashFile = ".venv\.req_hash"
    $curReqHash = if (Test-Path 'requirements.txt') { (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash } else { "" }
    $savedReqHash = if (Test-Path $reqHashFile) { (Get-Content $reqHashFile -Raw).Trim() } else { "" }
    if ($curReqHash -and ($curReqHash -ne $savedReqHash)) {
        Write-Host "  [ 20%] [####----------------] Phat hien cap nhat requirements.txt, dang dong bo thu vien..." -ForegroundColor Cyan
        & $py -m pip install -r requirements.txt --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [LOI] Cap nhat thu vien pip that bai! Kiem tra ket noi mang va thu lai." -ForegroundColor Red
            try { Read-Host "  Nhan Enter de thoat" } catch {}
            exit 1
        }
        $curReqHash | Set-Content $reqHashFile -NoNewline
        Write-Host "  [OK] Da cap nhat thu vien moi!" -ForegroundColor Green
    }
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
$errLog = Join-Path (Get-Location).Path "server.log"
$serverProc = $null
if (Test-Path $pyw) {
    $serverProc = Start-Process -FilePath $pyw -ArgumentList "server.py" -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
} else {
    $serverProc = Start-Process -FilePath $py -ArgumentList "server.py" -WorkingDirectory (Get-Location).Path -WindowStyle Minimized -PassThru
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

    if ($serverProc -and $serverProc.HasExited) {
        Write-Host ""
        Write-Host ("  [LOI] server.py da thoat dot ngot (ma loi: " + $serverProc.ExitCode + ").") -ForegroundColor Red
        if (Test-Path $errLog) {
            $errText = (Get-Content $errLog -Tail 15 -ErrorAction SilentlyContinue) -join "`n"
            if ($errText.Trim()) {
                Write-Host "  --- Chi tiet loi (server.log) ---" -ForegroundColor Yellow
                Write-Host $errText -ForegroundColor Gray
            }
        }
        try { Read-Host "  Nhan Enter de thoat" } catch {}
        exit 1
    }

    Start-Sleep -Milliseconds 250
}

Write-Host ""
Write-Host "  [CANH BAO] Het thoi gian cho server (cong 7860 khong phan hoi)." -ForegroundColor Red
try { Read-Host "  Nhan Enter de thoat" } catch {}
exit 1
