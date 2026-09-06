@echo off
REM One-click launcher for ReelVault (double-click me)
cd /d "%~dp0"
where powershell >nul 2>&1
if %errorlevel%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-ReelVault.ps1"
) else (
    echo PowerShell not found. Please install Windows PowerShell.
    pause
)
