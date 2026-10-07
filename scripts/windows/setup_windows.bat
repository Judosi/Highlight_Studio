@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0..\.."

echo Highlight Studio setup v11.2.7
if not exist ".highlight_studio\logs" mkdir ".highlight_studio\logs"
set "LOG=.highlight_studio\logs\setup.log"
> "%LOG%" echo setup log

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
    pause
    exit /b 1
)
%PYLAUNCH% tools\diagnostics\check_python_version.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Unsupported Python version. Use Python 3.10-3.13.
    type "%LOG%"
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" %PYLAUNCH% -m venv .venv >> "%LOG%" 2>&1
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install -r backend\requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Setup failed. Log:
    type "%LOG%"
    pause
    exit /b 1
)
"%PY%" tools\diagnostics\check_deps_fast.py backend\requirements.txt ".highlight_studio\deps_ok_v101513.txt" --write >> "%LOG%" 2>&1

REM Optional NVIDIA acceleration: auto-install into the private venv when a GPU is present.
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
    echo NVIDIA GPU detected. Checking CTranslate2 CUDA backend...
    "%PY%" tools\diagnostics\gpu_runtime_check.py --require-gpu >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo Installing optional NVIDIA runtime for faster-whisper...
        "%PY%" -m pip install -r backend\requirements-gpu-windows.txt >> "%LOG%" 2>&1
        if errorlevel 1 (
            echo [WARNING] NVIDIA runtime could not be installed. CPU fallback remains available.
        ) else (
            "%PY%" tools\diagnostics\gpu_runtime_check.py --require-gpu >> "%LOG%" 2>&1
            if errorlevel 1 (
                echo [WARNING] NVIDIA detected, but CTranslate2 CUDA backend is not ready. CPU fallback remains available.
            ) else (
                echo CTranslate2 CUDA backend OK; Whisper model verification happens on first use.
            )
        )
    ) else (
        echo CTranslate2 CUDA backend already OK; Whisper model verification happens on first use.
    )
)
if exist "frontend\dist\index.html" (
    echo Frontend dist OK. Node/Vite not needed for normal launch.
) else (
    echo [ERROR] frontend dist missing. This ZIP is incomplete.
    pause
    exit /b 1
)
echo Setup OK. Now run START_HERE.bat from the project root
echo START_HERE.bat will also verify FFmpeg and FFprobe before launch.
pause
