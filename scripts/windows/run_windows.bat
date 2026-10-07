@echo off
REM Highlight Studio v11.2.7 Deep Studio redesign
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0..\.."
REM Source/ZIP launch must behave as a true portable build.
REM This keeps Twitch fragments on the same drive as the program instead of
REM silently writing them to Videos/OneDrive, which can reduce throughput.
set "HIGHLIGHT_STUDIO_PORTABLE=1"
set "HIGHLIGHT_STUDIO_APP_ROOT=%CD%"
set "HIGHLIGHT_STUDIO_DATA_DIR=%CD%\.highlight_studio"
set "HIGHLIGHT_STUDIO_PROJECTS_DIR=%CD%\projects"
REM Bundled Twitch Turbo tools: no manual PATH setup needed.
if exist "vendor\twitchdownloadercli" set "PATH=%CD%\vendor\twitchdownloadercli;%PATH%"
if exist "vendor\aria2" set "PATH=%CD%\vendor\aria2;%PATH%"
REM A future licensed bundle may place FFmpeg here; otherwise the prerequisite
REM gate below uses a system installation from PATH.
if exist "vendor\ffmpeg\bin\ffmpeg.exe" set "PATH=%CD%\vendor\ffmpeg\bin;%PATH%"
title Highlight Studio v11.2.7 Hybrid Desktop

if not exist ".highlight_studio" mkdir ".highlight_studio"
if not exist ".highlight_studio\logs" mkdir ".highlight_studio\logs"
set "LOG=.highlight_studio\logs\startup.log"

> "%LOG%" echo Highlight Studio startup log
>> "%LOG%" echo Folder: "%CD%"
>> "%LOG%" echo Date: %DATE% %TIME%

echo ======================================================
echo Highlight Studio v11.2.7 - Hybrid Desktop
echo ======================================================
echo.
echo This launcher does NOT use Vite/Node for normal launch.
echo UI will be served by backend on a release-specific port starting at 8154
echo.

REM ---- Python detection ----
echo [1/7] Checking Python...
set "PYLAUNCH="
REM Prefer a supported Python even when a newer unsupported Python (for example 3.14) is also installed.
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYLAUNCH (
        py -%%V --version >nul 2>nul
        if not errorlevel 1 set "PYLAUNCH=py -%%V"
    )
)
if not defined PYLAUNCH (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYLAUNCH=python"
)
if not defined PYLAUNCH (
    echo [ERROR] Supported Python 3.10-3.13 not found.
    echo Install Python from https://www.python.org/downloads/ and enable Add to PATH.
    echo.
    echo Log file: %CD%\%LOG%
    pause
    exit /b 1
)
%PYLAUNCH% --version
%PYLAUNCH% --version >> "%LOG%" 2>&1
%PYLAUNCH% tools\diagnostics\check_python_version.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Unsupported Python version. Use Python 3.10-3.13.
    type "%LOG%"
    pause
    exit /b 1
)

REM ---- Release identity ----
echo.
echo [2/7] Verifying the exact v11.2.7 package and redesigned frontend...
%PYLAUNCH% tools\diagnostics\verify_release_identity.py "%CD%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] This folder does not contain the verified v11.2.7 interface.
    echo Do not merge this release with an older Highlight Studio folder.
    echo Extract Highlight_Studio_11.2.7.zip into a new folder and try again.
    type "%LOG%"
    pause
    exit /b 1
)
echo Release identity OK: v11.2.7 + sidebar + Review Studio.

REM ---- Required media tools ----
echo.
echo [3/7] Checking FFmpeg and FFprobe...
where ffmpeg >nul 2>nul
if errorlevel 1 goto :ffmpeg_missing
where ffprobe >nul 2>nul
if errorlevel 1 goto :ffmpeg_missing
ffmpeg -version >> "%LOG%" 2>&1
if errorlevel 1 goto :ffmpeg_missing
ffprobe -version >> "%LOG%" 2>&1
if errorlevel 1 goto :ffmpeg_missing
echo FFmpeg and FFprobe found.
goto :ffmpeg_ready

:ffmpeg_missing
echo [ERROR] FFmpeg or FFprobe is missing or cannot start.
echo Highlight Studio cannot import, analyze, preview or render video without both tools.
echo Run commands\setup\INSTALL_FFMPEG.bat, then close and reopen this launcher.
echo.
echo Log file: %CD%\%LOG%
pause
exit /b 1

:ffmpeg_ready
REM ---- Frontend dist check ----
echo.
echo [4/7] Checking prebuilt web UI...
if not exist "frontend\dist\index.html" (
    echo [ERROR] frontend\dist\index.html is missing.
    echo This archive is incomplete. Download the fixed ZIP again.
    echo.
    echo Log file: %CD%\%LOG%
    pause
    exit /b 1
)
echo Web UI found.

REM ---- Virtual environment ----
echo.
echo [5/7] Preparing Python virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv ...
    %PYLAUNCH% -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not create Python virtual environment.
        echo See log: %CD%\%LOG%
        type "%LOG%"
        pause
        exit /b 1
    )
)
set "PY=.venv\Scripts\python.exe"
"%PY%" --version
"%PY%" --version >> "%LOG%" 2>&1

REM ---- Backend dependencies ----
echo.
echo [6/7] Checking backend dependencies cache...
set "DEPS_STAMP=.highlight_studio\deps_ok_v101513.txt"
"%PY%" tools\diagnostics\check_deps_fast.py backend\requirements.txt "%DEPS_STAMP%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo Dependencies changed or missing. Installing once...
    "%PY%" -m pip install -r backend\requirements.txt >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Backend dependencies failed to install.
        echo Check internet connection and Python installation.
        echo.
        echo Log file: %CD%\%LOG%
        type "%LOG%"
        pause
        exit /b 1
    )
    "%PY%" tools\diagnostics\check_deps_fast.py backend\requirements.txt "%DEPS_STAMP%" --write >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Dependencies installed but import check failed.
        echo Log file: %CD%\%LOG%
        type "%LOG%"
        pause
        exit /b 1
    )
) else (
    echo Dependencies OK. Skipping pip install.
)

REM ---- Optional NVIDIA runtime ----
echo.
echo [GPU] Detecting NVIDIA acceleration...
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo No NVIDIA GPU detected. Auto mode will use the safe CPU path.
) else (
    "%PY%" tools\diagnostics\gpu_runtime_check.py --require-gpu >> "%LOG%" 2>&1
    if errorlevel 1 (
        set "GPU_ATTEMPT=.highlight_studio\gpu_setup_attempted_v101513.txt"
        if not exist "!GPU_ATTEMPT!" (
            echo NVIDIA found; preparing CTranslate2 CUDA backend once...
            "%PY%" -m pip install -r backend\requirements-gpu-windows.txt >> "%LOG%" 2>&1
            > "!GPU_ATTEMPT!" echo %DATE% %TIME%
        )
        "%PY%" tools\diagnostics\gpu_runtime_check.py --require-gpu >> "%LOG%" 2>&1
        if errorlevel 1 (
            echo [WARNING] NVIDIA is present, but CTranslate2 CUDA backend is unavailable.
            echo Auto mode will safely use CPU. See commands\setup\INSTALL_GPU_ACCELERATION.bat to retry after a driver update.
        ) else (
            echo CTranslate2 CUDA backend ready; Whisper will verify the model on first use.
        )
    ) else (
        echo CTranslate2 CUDA backend ready; Whisper will verify the model on first use.
    )
)

REM ---- Backend import test ----
echo.
echo [7/7] Checking backend import...
"%PY%" -c "import fastapi, uvicorn; import backend.src.highlight_studio.api.app; print('Backend import OK')" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Backend failed to import.
    echo Log file: %CD%\%LOG%
    type "%LOG%"
    pause
    exit /b 1
)
echo Backend import OK.

REM ---- Isolated runtime port ----
echo.
echo Selecting a free local port for v11.2.7...
set "HS_PORT="
set "HS_PORT_FILE=.highlight_studio\selected_port.tmp"
"%PY%" tools\diagnostics\select_local_port.py 8154 8199 > "%HS_PORT_FILE%"
if errorlevel 1 (
    if exist "%HS_PORT_FILE%" del /q "%HS_PORT_FILE%" >nul 2>nul
    echo [ERROR] Could not select a local port.
    pause
    exit /b 1
)
set /p HS_PORT=<"%HS_PORT_FILE%"
del /q "%HS_PORT_FILE%" >nul 2>nul
if not defined HS_PORT (
    echo [ERROR] No free local port was found in range 8154-8199.
    echo Close old Highlight Studio windows and try again.
    pause
    exit /b 1
)
set "HIGHLIGHT_STUDIO_PORT=%HS_PORT%"
set "HS_URL=http://127.0.0.1:%HS_PORT%"
if not defined HIGHLIGHT_STUDIO_OPEN_PATH set "HIGHLIGHT_STUDIO_OPEN_PATH=/"
> ".highlight_studio\runtime_port.txt" echo %HS_PORT%
>> "%LOG%" echo Runtime port: %HS_PORT%
>> "%LOG%" echo Runtime URL: %HS_URL%

echo.
echo ======================================================
echo Starting Highlight Studio v11.2.7...
echo Open in browser: %HS_URL%
echo The old v10.14.0 address on port 8133 will not be used.
echo Browser opening is blocked unless backend and frontend both report v11.2.7.
echo To stop server: press CTRL+C in this window.
REM Access logs are disabled in normal mode so the console stays clean.
echo If something fails, START_HERE.bat will keep the window open and show the log.
echo Log file: %CD%\%LOG%
echo ======================================================
echo.

start "" /B "%PY%" tools\diagnostics\wait_and_open.py "%HS_URL%" "%HIGHLIGHT_STUDIO_OPEN_PATH%" "v11.2.7-quality-recovery-audit" "studio-audited-v15"
"%PY%" -m uvicorn backend.src.highlight_studio.api.app:app --host 127.0.0.1 --port %HS_PORT% --no-access-log
set "HS_SERVER_EXIT=%ERRORLEVEL%"

echo.
echo Server stopped or crashed with exit code %HS_SERVER_EXIT%.
echo If this was unexpected, send me this log file:
echo "%CD%\%LOG%"
echo.
pause
endlocal & exit /b %HS_SERVER_EXIT%
