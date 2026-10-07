@echo off
setlocal
cd /d "%~dp0\..\..\.."
set "HS_ROOT=%CD%"
python tools\diagnostics\verify_release_identity.py "%HS_ROOT%"
if errorlevel 1 exit /b 1
python -m pytest -q tests\test_longform_target_integrity_1124.py tests\test_micro_ai_recovery_1123.py tests\test_duration_and_review_feedback_1122.py
if errorlevel 1 exit /b 1
echo Highlight Studio 11.2.4 long-form target integrity check: OK
