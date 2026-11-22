@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0installer-regie-windows.ps1" %*
set "install_exit=%errorlevel%"
if not "%install_exit%"=="0" (
    echo.
    echo [SoundLive-NDI] Installation interrompue (code %install_exit%).
    echo Journal : "%LOCALAPPDATA%\SoundLive-NDI-installation.log"
    echo Le message d'erreur reste affiche ci-dessus.
    pause
)
exit /b %install_exit%
