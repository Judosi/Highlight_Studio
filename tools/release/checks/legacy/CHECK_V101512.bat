@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.12 UX Workflow package...
if not exist "release_identity.json" echo [ERROR] release identity missing & set "ERR=1"
if not exist "tests\test_ux_workflow_101512.py" echo [ERROR] UX workflow tests missing & set "ERR=1"
if not exist "frontend\tests\uxWorkflow101512.contract.test.js" echo [ERROR] frontend UX contract missing & set "ERR=1"
if not exist "frontend\dist\ux-workflow-101512.js" echo [ERROR] packaged UX layer missing & set "ERR=1"
if not exist "frontend\dist\studio-final-101512.css" echo [ERROR] studio-final-101512.css missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101512_RU.md" echo [ERROR] change report missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.12 files are present.
exit /b %ERR%
