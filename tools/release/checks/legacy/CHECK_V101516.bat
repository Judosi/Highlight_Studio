@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Checking Highlight Studio v10.15.16 Review Unlock Hotfix...
echo.
set "ERR=0"
if not exist "BUILD_INFO_V101516_RU.txt" echo [ERROR] BUILD_INFO_V101516_RU.txt missing & set "ERR=1"
if not exist "CHANGE_REPORT_V101516_RU.md" echo [ERROR] CHANGE_REPORT_V101516_RU.md missing & set "ERR=1"
if not exist "tests\test_review_unlock_101516.py" echo [ERROR] regression test missing & set "ERR=1"
if not exist "frontend\dist\assets\index-101516-283835ef15a9.js" echo [ERROR] 10.15.16 frontend bundle missing & set "ERR=1"
if "%ERR%"=="0" (
  py -3.12 tools\diagnostics\verify_release_identity.py "%CD%" >nul 2>nul
  if errorlevel 1 py tools\diagnostics\verify_release_identity.py "%CD%" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Release identity verification failed.
    set "ERR=1"
  ) else (
    echo [OK] Release identity is v10.15.16.
  )
)
if "%ERR%"=="0" echo [OK] Required v10.15.16 hotfix files are present.
echo.
if not "%ERR%"=="0" (
  echo Package check FAILED.
  pause
  exit /b 1
)
echo Package check PASSED.
pause
exit /b 0
