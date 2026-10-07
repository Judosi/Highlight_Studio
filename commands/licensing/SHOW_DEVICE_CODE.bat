@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".highlight_studio" mkdir ".highlight_studio" >nul 2>nul

echo ======================================================
echo Highlight Studio - код устройства
echo ======================================================
echo.

set "PY_CMD="
if exist ".venv\Scripts\python.exe" set "PY_CMD=.venv\Scripts\python.exe"
if not defined PY_CMD (
  py -3.13 --version >nul 2>nul && set "PY_CMD=py -3.13"
)
if not defined PY_CMD (
  py -3.12 --version >nul 2>nul && set "PY_CMD=py -3.12"
)
if not defined PY_CMD (
  py -3.11 --version >nul 2>nul && set "PY_CMD=py -3.11"
)
if not defined PY_CMD (
  py -3.10 --version >nul 2>nul && set "PY_CMD=py -3.10"
)
if not defined PY_CMD (
  python --version >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
  echo [ERROR] Python 3.10-3.13 не найден.
  echo Сначала запусти START_HERE.bat или установи Python.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%D in (`call %PY_CMD% tools\licensing\device_code.py`) do set "DEVICE_CODE=%%D"
if not defined DEVICE_CODE (
  echo [ERROR] Не удалось получить код устройства.
  pause
  exit /b 1
)

echo Полный код устройства:
echo.
echo %DEVICE_CODE%
echo.
> ".highlight_studio\device_code.txt" echo %DEVICE_CODE%
echo Он также сохранён в:
echo %CD%\.highlight_studio\device_code.txt
echo.
echo Этот код можно вставить в генератор лицензии владельца.
echo.
pause
endlocal
