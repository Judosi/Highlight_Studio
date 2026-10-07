@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
REM Debug backend with full access logs. Use only when diagnosing API calls.
if exist "vendor\twitchdownloadercli" set "PATH=%CD%\vendor\twitchdownloadercli;%PATH%"
if exist "vendor\aria2" set "PATH=%CD%\vendor\aria2;%PATH%"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m uvicorn backend.src.highlight_studio.api.app:app --host 127.0.0.1 --port 8000 --access-log
) else (
    echo .venv not found. Run START_HERE.bat first.
)
pause
