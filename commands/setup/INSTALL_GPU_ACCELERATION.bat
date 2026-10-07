@echo off
chcp 65001 >nul
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
title Highlight Studio 11.2.7 - NVIDIA GPU Setup

echo ======================================================
echo Highlight Studio 11.2.7 - NVIDIA GPU acceleration
echo ======================================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. First run START_HERE.bat once.
  pause
  exit /b 1
)
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo [ERROR] NVIDIA driver / nvidia-smi not found.
  echo Install or update the NVIDIA driver first. The app can still work on CPU.
  pause
  exit /b 2
)
set "PY=.venv\Scripts\python.exe"
echo Current hardware status:
"%PY%" tools\diagnostics\gpu_runtime_check.py

echo.
echo Installing CUDA runtime libraries into Highlight Studio's private .venv.
echo This download is large, but it does not modify the global Python environment.
"%PY%" -m pip install -r backend\requirements-gpu-windows.txt
if errorlevel 1 (
  echo.
  echo [ERROR] GPU runtime installation failed. Highlight Studio remains usable in CPU mode.
  pause
  exit /b 3
)

echo.
echo Re-checking GPU runtime...
"%PY%" tools\diagnostics\gpu_runtime_check.py --require-gpu
if errorlevel 1 (
  echo.
  echo [WARNING] NVIDIA is present, but CTranslate2 CUDA is still unavailable.
  echo Update the NVIDIA driver, then run this file again. CPU mode remains available.
  pause
  exit /b 4
)

echo.
echo [OK] CTranslate2 CUDA backend is ready. Highlight Studio Auto mode will verify Whisper model startup on first use.
pause
exit /b 0
