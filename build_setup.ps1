$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pyinstaller = Join-Path $PSScriptRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller not found in .venv\Scripts\pyinstaller.exe"
}

Write-Host "[1/2] Building HaiAI Studio desktop bundle with PyInstaller..."
& $pyinstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "HaiAI Studio" `
    --paths "MuseAI-API" `
    --add-data "static;static" `
    --collect-submodules muse_ai `
    --collect-all imageio_ffmpeg `
    --collect-all curl_cffi `
    --collect-all webview `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    desktop_app.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$isccCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) not found."
}

Write-Host "[2/2] Packaging Windows installer (dist\HaiAI_Studio_Setup.exe)..."
& $iscc "installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}

Write-Host "DONE: dist\HaiAI_Studio_Setup.exe"
