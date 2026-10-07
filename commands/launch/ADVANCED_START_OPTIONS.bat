@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
title Highlight Studio v11.2.7 - Advanced Options
:menu
cls
echo Highlight Studio v11.2.7 - Advanced Options
echo.
echo [1] Start main application
echo [2] Start and open YouTube publishing
echo [3] Verify running version
echo [4] Start Web Local mode
echo [5] Stop Web Local mode
echo [6] Install / repair NVIDIA acceleration
echo [7] Install FFmpeg prerequisite
echo [8] Exit
echo.
choice /C 12345678 /N /M "Select [1-8]: "
if errorlevel 8 goto :end
if errorlevel 7 call "%HS_ROOT%\commands\setup\INSTALL_FFMPEG.bat"&goto :after
if errorlevel 6 call "%HS_ROOT%\commands\setup\INSTALL_GPU_ACCELERATION.bat"&goto :after
if errorlevel 5 call "%HS_ROOT%\commands\launch\STOP_WEB_LOCAL.bat"&goto :after
if errorlevel 4 call "%HS_ROOT%\commands\launch\START_WEB_LOCAL.bat"&goto :after
if errorlevel 3 call "%HS_ROOT%\commands\diagnostics\VERIFY_RUNNING_VERSION.bat"&goto :after
if errorlevel 2 call "%HS_ROOT%\commands\launch\START_YOUTUBE_READY.bat"&goto :after
if errorlevel 1 call "%HS_ROOT%\START_HERE.bat"&goto :after
:after
echo.
pause
goto :menu
:end
endlocal & exit /b 0
