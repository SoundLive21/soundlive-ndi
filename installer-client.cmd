@echo off
rem SoundLive-NDI
rem Auteur : DUVERNOY Matthieu ^& ChatGPT
rem contact@soundlive.fr
rem Société SoundLive
setlocal
cd /d "%~dp0"
where py.exe >nul 2>nul
if errorlevel 1 goto :python
py -3 "%~dp0installer-client-windows.py" %*
exit /b %errorlevel%
:python
python "%~dp0installer-client-windows.py" %*
exit /b %errorlevel%
