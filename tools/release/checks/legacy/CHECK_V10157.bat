@echo off
setlocal
set "ERR=0"
echo Checking Highlight Studio v10.15.7 package...
if not exist "release_identity.json" echo [ERROR] release_identity.json missing & set "ERR=1"
if not exist "frontend\dist\index.html" echo [ERROR] frontend dist missing & set "ERR=1"
if not exist "frontend\dist\studio-final-10157.css" echo [ERROR] studio-final-10157.css missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\hardware.py" echo [ERROR] hardware optimizer missing & set "ERR=1"
if not exist "backend\src\highlight_studio\services\pipeline.py" echo [ERROR] pipeline missing & set "ERR=1"
if not exist "tests\test_hardware_optimizer_10157.py" echo [ERROR] 10.15.7 regression tests missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.7 quality-performance files are present.
exit /b %ERR%
