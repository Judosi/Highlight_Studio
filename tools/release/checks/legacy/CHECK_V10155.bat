@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Checking Highlight Studio v10.15.5 package...
set "ERR=0"
if not exist "START_HERE.bat" echo [ERROR] START_HERE.bat missing & set "ERR=1"
if not exist "frontend\dist\index.html" echo [ERROR] frontend dist missing & set "ERR=1"
if not exist "frontend\dist\studio-final-10155.css" echo [ERROR] studio-final-10155.css missing & set "ERR=1"
if not exist "release_identity.json" echo [ERROR] release_identity.json missing & set "ERR=1"
if not exist "backend\src\highlight_studio\api\app.py" echo [ERROR] backend app missing & set "ERR=1"
if not exist "tests\test_analysis_start_10155.py" echo [ERROR] analysis-start regression tests missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.5 files are present.
echo.
echo You can also run VERIFY_RUNNING_VERSION.bat after START_HERE.bat.
pause
endlocal & exit /b %ERR%
