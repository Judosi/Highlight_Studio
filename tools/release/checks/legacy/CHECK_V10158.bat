@echo off
setlocal
set "ERR=0"
echo Checking Highlight Studio v10.15.8 package...
if not exist "release_identity.json" echo [ERROR] release_identity.json missing & set "ERR=1"
if not exist "frontend\dist\index.html" echo [ERROR] frontend dist missing & set "ERR=1"
if not exist "frontend\dist\studio-final-10158.css" echo [ERROR] studio-final-10158.css missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\hardware.py" echo [ERROR] hardware optimizer missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\pipeline.py" echo [ERROR] pipeline missing & set "ERR=1"
if not exist "tests\test_manual_review_navigation_10158.py" echo [ERROR] 10.15.8 manual navigation regression tests missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.8 manual-review files are present.
exit /b %ERR%
