@echo off
chcp 65001 >nul
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
title Highlight Studio - FFmpeg prerequisite

echo ======================================================
echo Highlight Studio - Install FFmpeg prerequisite
echo ======================================================
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 goto :install
where ffprobe >nul 2>nul
if errorlevel 1 goto :install
ffmpeg -version >nul 2>nul
if errorlevel 1 goto :install
ffprobe -version >nul 2>nul
if errorlevel 1 goto :install

echo [OK] FFmpeg and FFprobe are already available.
echo You can run START_HERE.bat from the application root.
pause
exit /b 0

:install
where winget >nul 2>nul
if errorlevel 1 goto :manual

echo FFmpeg and FFprobe were not found.
echo Windows Package Manager will now install Gyan.FFmpeg.
echo You may see a standard Windows confirmation prompt.
echo.
winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :failed

echo.
echo Installation command completed.
echo Close this window, then reopen START_HERE.bat so Windows refreshes PATH.
pause
exit /b 0

:manual
echo [ERROR] Windows Package Manager ^(winget^) is unavailable.
echo Install a trusted FFmpeg build using the official download page:
echo https://ffmpeg.org/download.html
echo.
echo Both ffmpeg.exe and ffprobe.exe must be available in PATH.
pause
exit /b 1

:failed
echo.
echo [ERROR] FFmpeg installation did not complete.
echo Retry this file as a normal user or install from:
echo https://ffmpeg.org/download.html
pause
exit /b 1
