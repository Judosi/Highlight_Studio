@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.10 AI Runtime package...
if not exist "release_identity.json" echo [ERROR] release_identity.json missing & set "ERR=1"
if not exist "backend\src\highlight_studio\integrations\ai\runtime.py" echo [ERROR] AI runtime missing & set "ERR=1"
if not exist "tests\test_ai_runtime_101510.py" echo [ERROR] AI runtime tests missing & set "ERR=1"
if not exist "frontend\dist\studio-final-101510.css" echo [ERROR] studio-final-101510.css missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101510_RU.md" echo [ERROR] change report missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.10 files are present.
exit /b %ERR%
