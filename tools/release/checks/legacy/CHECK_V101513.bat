@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.13 Phase 1 Remediation package...
if not exist "release_identity.json" echo [ERROR] release identity missing & set "ERR=1"
if not exist "tests\test_ux_workflow_101513.py" echo [ERROR] UX workflow tests missing & set "ERR=1"
if not exist "frontend\tests\uxWorkflow101513.contract.test.js" echo [ERROR] frontend UX contract missing & set "ERR=1"
if not exist "frontend\dist\ux-workflow-101513.js" echo [ERROR] packaged UX layer missing & set "ERR=1"
if not exist "frontend\dist\studio-final-101513.css" echo [ERROR] studio-final-101513.css missing & set "ERR=1"
if not exist "tools\diagnostics\test_manual_step_101513.py" echo [ERROR] manual step regression missing & set "ERR=1"
if not exist "tests\test_phase1_101513.py" echo [ERROR] Phase 1 backend tests missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101513_RU.md" echo [ERROR] change report missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.13 files are present.
exit /b %ERR%
