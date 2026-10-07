@echo off
setlocal
cd /d "%~dp0"
set "ERR=0"
echo Checking Highlight Studio v10.15.17 Post-process Reliability...
if not exist "BUILD_INFO_V101517_RU.txt" echo [ERROR] BUILD_INFO_V101517_RU.txt missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101517_RU.md" echo [ERROR] CHANGE_REPORT_V101517_RU.md missing & set "ERR=1"
if not exist "tests\test_postprocess_reliability_101517.py" echo [ERROR] regression test missing & set "ERR=1"
for %%F in (frontend\dist\assets\index-101517-*.js) do set "HS_BUNDLE=%%F"
if not defined HS_BUNDLE echo [ERROR] 10.15.17 frontend bundle missing & set "ERR=1"
python -c "import json; d=json.load(open('release_identity.json',encoding='utf-8')); raise SystemExit(0 if d.get('version')=='10.15.17' and d.get('app_version')=='v10.15.17-postprocess-reliability' else 1)"
if errorlevel 1 echo [ERROR] release_identity.json mismatch & set "ERR=1"
if "%ERR%"=="0" echo [OK] Required v10.15.17 reliability files are present.
exit /b %ERR%
