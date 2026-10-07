@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\..\frontend"

echo Dev frontend mode. Обычно он НЕ нужен: START_HERE.bat уже открывает готовый UI на http://127.0.0.1:8000

echo Checking Node/npm...
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js не найден. Для обычного запуска он не нужен. Используй START_HERE.bat.
    pause
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm не найден. Для обычного запуска он не нужен. Используй START_HERE.bat.
    pause
    exit /b 1
)

echo Installing frontend dependencies if needed...
if not exist node_modules\react\package.json call npm install --no-audit --no-fund
if not exist node_modules\.bin\vite.cmd call npm install --no-audit --no-fund

call .\node_modules\.bin\vite.cmd --host 127.0.0.1 --port 5173
pause
