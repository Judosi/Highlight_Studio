@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
title Highlight Studio v11.2.7 - YouTube Publishing
set "HIGHLIGHT_STUDIO_OPEN_PATH=/youtube-publisher.html"
call "%HS_ROOT%\START_HERE.bat"
set "HS_EXIT=%ERRORLEVEL%"
endlocal & exit /b %HS_EXIT%
