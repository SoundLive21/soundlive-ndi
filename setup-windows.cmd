@echo off
setlocal
cd /d "%~dp0"
echo SoundLive NDI - Installation Windows
echo 1. Regie
echo 2. Lecteur (raccordement ulterieur)
echo 3. Quitter
choice /c 123 /n /m "Choix : "
if errorlevel 3 exit /b 0
if errorlevel 2 goto player
call "%~dp0installer-regie-windows.cmd"
exit /b %errorlevel%
:player
call "%~dp0installer-client.cmd" --installer-seul
if errorlevel 1 (
  echo Installation interrompue. Verifie les prerequis et le message ci-dessus.
  pause
  exit /b 1
)
echo Installation terminee. Ouvre SoundLive-NDI - Raccorder le lecteur dans le menu Demarrer.
pause
