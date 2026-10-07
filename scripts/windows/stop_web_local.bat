@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "WEB_ENV=.highlight_studio\web_local.env"

echo Stopping Highlight Studio local PostgreSQL...
where docker >nul 2>&1
if errorlevel 1 (
    echo Docker CLI not found. Nothing was changed.
    pause
    exit /b 1
)
if not exist "%WEB_ENV%" (
    echo Local web configuration does not exist yet.
    pause
    exit /b 0
)
docker compose -p highlight-studio-local-web --env-file "%WEB_ENV%" -f deploy\docker-compose.local-web.yml down
if errorlevel 1 (
    echo Could not stop the container. Open Docker Desktop and try again.
    pause
    exit /b 1
)
echo PostgreSQL stopped. Accounts and projects remain saved.
pause
