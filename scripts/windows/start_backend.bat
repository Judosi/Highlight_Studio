@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
REM Bundled Twitch Turbo tools: no manual PATH setup needed.
if exist "vendor\twitchdownloadercli" set "PATH=%CD%\vendor\twitchdownloadercli;%PATH%"
if exist "vendor\aria2" set "PATH=%CD%\vendor\aria2;%PATH%"
echo Starting backend only through venv if available...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m uvicorn backend.src.highlight_studio.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
) else (
    echo .venv not found. Run START_HERE.bat first.
)
pause
