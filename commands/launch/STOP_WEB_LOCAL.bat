@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
call "%HS_ROOT%\scripts\windows\stop_web_local.bat"
set "HS_EXIT=%ERRORLEVEL%"
endlocal & exit /b %HS_EXIT%
