@echo off
setlocal
cd /d "%~dp0\..\..\.."
set "HS_ROOT=%CD%"
python tools\diagnostics\verify_release_identity.py "%HS_ROOT%"
if errorlevel 1 exit /b 1
python -m pytest -q tests\test_stream_specific_creator_pack_1125.py tests\test_longform_target_integrity_1124.py tests\test_micro_ai_recovery_1123.py
if errorlevel 1 exit /b 1
echo Highlight Studio 11.2.6 stream-specific Creator Pack check: OK
