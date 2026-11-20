@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0installer-regie-windows.ps1" %*
exit /b %errorlevel%
