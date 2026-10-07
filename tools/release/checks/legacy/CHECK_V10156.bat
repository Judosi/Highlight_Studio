@echo off
setlocal
cd /d "%~dp0"
echo Checking Highlight Studio v10.15.6 package...
set "ERR=0"
if not exist "frontend\dist\index.html" echo [ERROR] frontend dist missing & set "ERR=1"
if not exist "frontend\dist\studio-final-10156.css" echo [ERROR] studio-final-10156.css missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\hardware.py" echo [ERROR] hardware optimizer missing & set "ERR=1"
if not exist "backend\requirements-gpu-windows.txt" echo [ERROR] GPU requirements missing & set "ERR=1"
if not exist "tools\diagnostics\gpu_runtime_check.py" echo [ERROR] GPU diagnostics missing & set "ERR=1"
if not exist "tests\test_hardware_optimizer_10156.py" echo [ERROR] hardware regression tests missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.6 hardware-auto files are present.
exit /b %ERR%
