@echo off
setlocal
cd /d "%~dp0"
echo SoundLive NDI - Installation Windows
echo 1. Regie
echo 2. Lecteur (raccordement ulterieur)
echo 3. Desinstaller la regie
echo 4. Desinstaller le lecteur
echo 5. Quitter
choice /c 12345 /n /m "Choix : "
if errorlevel 5 exit /b 0
if errorlevel 4 goto uninstallplayer
if errorlevel 3 goto uninstallregie
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
echo Installation terminee. Conserve ce kit et execute la commande complete generee par Ajouter un ecran dans la regie.
pause

exit /b 0
:uninstallplayer
set "role=lecteur"
goto uninstall
:uninstallregie
set "role=regie"
:uninstall
echo 1. Programme seulement
echo 2. Programme et donnees
echo 3. Programme, donnees et nettoyage systeme dedie
echo 4. Simulation complete
choice /c 1234 /n /m "Choix : "
set "flags="
if errorlevel 4 goto simulate
if errorlevel 3 goto clean
if errorlevel 2 set "flags=--purger"
goto rununinstall
:simulate
set "flags=--purger --nettoyer-systeme --simulation"
goto rununinstall
:clean
set "flags=--purger --nettoyer-systeme"
:rununinstall
set "interpreter=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if exist "%interpreter%" goto localpython
where py.exe >nul 2>nul
if errorlevel 1 goto pythonpath
py -3 "%~dp0desinstaller_soundlive.py" %role% %flags%
goto finish
:localpython
"%interpreter%" "%~dp0desinstaller_soundlive.py" %role% %flags%
goto finish
:pythonpath
python "%~dp0desinstaller_soundlive.py" %role% %flags%
:finish
set "result=%errorlevel%"
pause
exit /b %result%
