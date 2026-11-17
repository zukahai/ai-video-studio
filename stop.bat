@echo off
title Stop 23zo AI Studio
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Where-Object { $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Da dung server 23zo AI Studio.
timeout /t 2 >nul
