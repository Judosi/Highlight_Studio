@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
echo Highlight Studio 10.15.19 release check
python tools\diagnostics\verify_release_identity.py "%HS_ROOT%"
if errorlevel 1 exit /b 1
python -m pytest -q tests\test_workflow_navigation_101519.py tests\test_folder_architecture_101519.py
exit /b %ERRORLEVEL%
