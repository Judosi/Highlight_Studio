@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.14 AI Reliability package...
if not exist "release_identity.json" echo [ERROR] release identity missing & set "ERR=1"
if not exist "tests\test_ai_reliability_101514.py" echo [ERROR] AI reliability tests missing & set "ERR=1"
if not exist "tests\test_remediation_phase2_101514.py" echo [ERROR] Phase 2 remediation tests missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101514_RU.md" echo [ERROR] 10.15.14 change report missing & set "ERR=1"
if not exist "REMEDIATION_AI_RELIABILITY_10.15.14_RU.md" echo [ERROR] AI remediation report missing & set "ERR=1"
if not exist "backend\src\highlight_studio\integrations\ai\runtime.py" echo [ERROR] AI Runtime missing & set "ERR=1"
if not exist "frontend\dist\index.html" echo [ERROR] production frontend missing & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.14 files are present.
exit /b %ERR%
