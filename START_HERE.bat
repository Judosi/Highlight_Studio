@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Highlight Studio v11.2.7 - Safe Launcher

call :locate_app_root
if errorlevel 1 goto :not_extracted

if not exist ".highlight_studio\logs" mkdir ".highlight_studio\logs" >nul 2>nul
set "HS_LAUNCH_LOG=.highlight_studio\logs\launcher.log"
> "%HS_LAUNCH_LOG%" echo Highlight Studio v11.2.7 launcher
>> "%HS_LAUNCH_LOG%" echo Started: %DATE% %TIME%
>> "%HS_LAUNCH_LOG%" echo Folder: "%CD%"

echo ======================================================
echo Highlight Studio v11.2.7
echo ======================================================
echo.
echo Starting the application from:
echo "%CD%"
echo.

call "%CD%\scripts\windows\run_windows.bat"
set "HS_EXIT=%ERRORLEVEL%"

echo.
echo ======================================================
if "%HS_EXIT%"=="0" (
    echo Highlight Studio was stopped normally.
) else (
    echo [ERROR] Highlight Studio exited with code %HS_EXIT%.
    echo Startup log:
    echo "%CD%\.highlight_studio\logs\startup.log"
    if exist ".highlight_studio\logs\startup.log" (
        echo.
        type ".highlight_studio\logs\startup.log"
    )
)
echo ======================================================
echo.
echo Press any key to close this window.
pause >nul
endlocal & exit /b %HS_EXIT%

:locate_app_root
call :check_required
if not errorlevel 1 exit /b 0

REM Windows "Extract All" can create a duplicate outer directory when the ZIP
REM already contains/was previously extracted into a folder with the same name.
REM Recover automatically from one or two nested Highlight_Studio folders.
for /d %%D in ("%CD%\Highlight_Studio_11.2.7*") do (
    if exist "%%~fD\scripts\windows\run_windows.bat" if exist "%%~fD\backend\requirements.txt" if exist "%%~fD\frontend\dist\index.html" (
        cd /d "%%~fD"
        exit /b 0
    )
    for /d %%E in ("%%~fD\Highlight_Studio_11.2.7*") do (
        if exist "%%~fE\scripts\windows\run_windows.bat" if exist "%%~fE\backend\requirements.txt" if exist "%%~fE\frontend\dist\index.html" (
            cd /d "%%~fE"
            exit /b 0
        )
    )
)
exit /b 1

:check_required
set "HS_MISSING=0"
if not exist "scripts\windows\run_windows.bat" set "HS_MISSING=1"
if not exist "backend\requirements.txt" set "HS_MISSING=1"
if not exist "frontend\dist\index.html" set "HS_MISSING=1"
exit /b !HS_MISSING!

:not_extracted
echo [ERROR] The application files are incomplete or the ZIP was not fully extracted.
echo.
echo Checked folder:
echo "%CD%"
echo.
echo Required files:
if not exist "scripts\windows\run_windows.bat" echo   [MISSING] scripts\windows\run_windows.bat
if not exist "backend\requirements.txt" echo   [MISSING] backend\requirements.txt
if not exist "frontend\dist\index.html" echo   [MISSING] frontend\dist\index.html
echo.
echo Do not run START_HERE.bat directly inside the ZIP archive.
echo.
echo FIX:
echo 1. Close this window.
echo 2. Right-click the ZIP and choose Extract All.
echo 3. Open the extracted folder, not the ZIP preview.
echo 4. Run START_HERE.bat there.
echo.
echo This launcher also detects one or two accidental nested Highlight_Studio folders.
echo.
echo Press any key to close this window.
pause >nul
endlocal & exit /b 2
