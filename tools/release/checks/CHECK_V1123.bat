@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..\..") do set "HS_ROOT=%%~fI"
cd /d "%HS_ROOT%"
echo Highlight Studio 11.2.3 release check
python tools\diagnostics\verify_release_identity.py "%HS_ROOT%"
if errorlevel 1 exit /b 1
python -m pytest -q tests\test_micro_ai_recovery_1123.py tests\test_ai_runtime_101512.py tests\test_ai_reliability_101514.py tests\test_duration_and_review_feedback_1122.py tests\test_reliability_1120.py tests\test_reliability_1110.py
exit /b %ERRORLEVEL%
