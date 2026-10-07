@echo off
setlocal
cd /d "%~dp0\..\..\.."
python tools\diagnostics\verify_release_identity.py .
if errorlevel 1 exit /b 1
python -m pytest -q tests\test_audit_regressions_1126.py tests\test_stream_specific_creator_pack_1125.py tests\test_longform_target_integrity_1124.py tests\test_micro_ai_recovery_1123.py
if errorlevel 1 exit /b 1
pushd frontend
node --test tests/audit1126.test.js
set "HS_CHECK_CODE=%ERRORLEVEL%"
popd
if not "%HS_CHECK_CODE%"=="0" exit /b %HS_CHECK_CODE%
echo Highlight Studio 11.2.6 quality and recovery checks: OK
