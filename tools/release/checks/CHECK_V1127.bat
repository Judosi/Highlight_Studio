@echo off
setlocal
cd /d "%~dp0\..\..\.."
call npm --prefix frontend ci
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1
python tools\diagnostics\verify_release_identity.py .
if errorlevel 1 exit /b 1
python -m compileall -q backend tools services tests
if errorlevel 1 exit /b 1
python -m ruff check backend tools tests services
if errorlevel 1 exit /b 1
python -m pytest -q
if errorlevel 1 exit /b 1
call npm --prefix frontend run lint
if errorlevel 1 exit /b 1
call npm --prefix frontend test
if errorlevel 1 exit /b 1
call npm --prefix frontend audit --audit-level=moderate
if errorlevel 1 exit /b 1
call npm --prefix desktop\electron test
if errorlevel 1 exit /b 1
call npm --prefix desktop\electron audit --package-lock-only --audit-level=moderate
if errorlevel 1 exit /b 1
python -m pip_audit -r backend\requirements.txt --progress-spinner off
if errorlevel 1 exit /b 1
python tools\release\make_release.py
if errorlevel 1 exit /b 1
python tools\release\verify_archive.py ..\Highlight_Studio_11.2.7.zip
if errorlevel 1 exit /b 1
echo Highlight Studio 11.2.7 automated checks: OK
echo Windows installer, GPU and real VOD acceptance must be recorded separately.
