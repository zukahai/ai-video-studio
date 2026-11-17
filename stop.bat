@echo off
title Stop 23zo AI Studio
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R ":7860 .*LISTENING"') do (
    taskkill /F /T /PID %%a >nul 2>&1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'Name like ''python%''' | Where-Object { $_.CommandLine -like '*server.py*' -or $_.CommandLine -like '*multiprocessing.spawn*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Da dung server 23zo AI Studio tren cong 7860.
timeout /t 2 >nul
