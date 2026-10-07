@echo off
setlocal
cd /d "%~dp0\..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\build_hybrid_release.ps1" %*
if errorlevel 1 pause
endlocal
