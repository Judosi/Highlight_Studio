@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
set "ERR=0"
if not exist "START_HERE.bat" echo [ERROR] START_HERE.bat missing & set "ERR=1"
if not exist "commands\launch\ADVANCED_START_OPTIONS.bat" echo [ERROR] commands/launch missing & set "ERR=1"
if not exist "commands\setup\INSTALL_GPU_ACCELERATION.bat" echo [ERROR] commands/setup missing & set "ERR=1"
if not exist "commands\diagnostics\VERIFY_RUNNING_VERSION.bat" echo [ERROR] commands/diagnostics missing & set "ERR=1"
if not exist "docs\ARCHITECTURE.md" echo [ERROR] docs/ARCHITECTURE.md missing & set "ERR=1"
if not exist "tools\release\release_layout.py" echo [ERROR] release_layout.py missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Highlight Studio 10.15.18 folder architecture is complete.
endlocal & exit /b %ERR%
