@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
set "HIGHLIGHT_STUDIO_OPEN_PATH=/"
call "%HS_ROOT%\START_HERE.bat"
set "HS_EXIT=%ERRORLEVEL%"
endlocal & exit /b %HS_EXIT%
