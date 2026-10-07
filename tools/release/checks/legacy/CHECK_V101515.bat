@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.15 Production Stabilization package...
if not exist "release_identity.json" echo [ERROR] release identity missing & set "ERR=1"
if not exist "tests\test_phase3_101515.py" echo [ERROR] Phase 3 tests missing & set "ERR=1"
if not exist "frontend\dist\ui-presentation-101515.js" echo [ERROR] presentation bridge missing & set "ERR=1"
if exist "frontend\dist\ux-workflow-101513.js" echo [ERROR] legacy stateful workflow script must not ship & set "ERR=1"
if not exist "CHANGE_REPORT_V101515_RU.md" echo [ERROR] change report missing & set "ERR=1"
if not exist "REMEDIATION_PRODUCTION_STABILIZATION_10.15.15_RU.md" echo [ERROR] remediation report missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.15 files are present.
exit /b %ERR%
