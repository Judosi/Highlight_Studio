@echo off
setlocal EnableExtensions
chcp 65001 >nul
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
set "HS_PORT=8154"
if exist ".highlight_studio\runtime_port.txt" set /p HS_PORT=<".highlight_studio\runtime_port.txt"
set "HS_YOUTUBE_URL=http://127.0.0.1:%HS_PORT%/youtube-publisher.html?hs_release=v11.2.7-quality-recovery-audit"
start "" "%HS_YOUTUBE_URL%"
if errorlevel 1 (
  echo.
  echo Не удалось открыть браузер автоматически.
  echo Откройте вручную: %HS_YOUTUBE_URL%
  pause
)
endlocal
