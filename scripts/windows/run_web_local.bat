@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0..\.."

set "HIGHLIGHT_STUDIO_PORTABLE=1"
set "HIGHLIGHT_STUDIO_APP_ROOT=%CD%"
set "HIGHLIGHT_STUDIO_DATA_DIR=%CD%\.highlight_studio\web"
set "HIGHLIGHT_STUDIO_PROJECTS_DIR=%CD%\projects_web"
if exist "vendor\twitchdownloadercli" set "PATH=%CD%\vendor\twitchdownloadercli;%PATH%"
if exist "vendor\aria2" set "PATH=%CD%\vendor\aria2;%PATH%"

title Highlight Studio v11.2.7 - Local Web + PostgreSQL
if not exist ".highlight_studio" mkdir ".highlight_studio"
if not exist ".highlight_studio\logs" mkdir ".highlight_studio\logs"
if not exist ".highlight_studio\web" mkdir ".highlight_studio\web"
if not exist "projects_web" mkdir "projects_web"
set "LOG=.highlight_studio\logs\web_local_startup.log"
set "WEB_ENV=.highlight_studio\web_local.env"

> "%LOG%" echo Highlight Studio Local Web startup log
>> "%LOG%" echo Folder: %CD%
>> "%LOG%" echo Date: %DATE% %TIME%

echo ======================================================
echo Highlight Studio v11.2.7 - Local Web
echo PostgreSQL + registration + accounts + project roles
echo ======================================================
echo.

REM ---- Python detection ----
echo [1/8] Checking Python...
set "PYLAUNCH="
REM Prefer a supported Python even when a newer unsupported Python (for example 3.14) is also installed.
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYLAUNCH (
        py -%%V --version >nul 2>nul
        if not errorlevel 1 set "PYLAUNCH=py -%%V"
    )
)
if not defined PYLAUNCH (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYLAUNCH=python"
)
if not defined PYLAUNCH (
    echo [ERROR] Python 3.10-3.13 not found.
    echo Install Python and enable Add to PATH.
    pause
    exit /b 1
)
%PYLAUNCH% tools\diagnostics\check_python_version.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Unsupported Python version. Use Python 3.10-3.13.
    type "%LOG%"
    pause
    exit /b 1
)

REM ---- UI and virtualenv ----
echo [2/8] Preparing application environment...
%PYLAUNCH% tools\diagnostics\verify_release_identity.py "%CD%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] The packaged frontend is not the verified v11.2.7 redesign.
    type "%LOG%"
    pause
    exit /b 1
)
if not exist "frontend\dist\index.html" (
    echo [ERROR] frontend\dist\index.html is missing.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    %PYLAUNCH% -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not create .venv. See %LOG%
        pause
        exit /b 1
    )
)
set "PY=.venv\Scripts\python.exe"
set "DEPS_STAMP=.highlight_studio\deps_ok_v101513.txt"
"%PY%" tools\diagnostics\check_deps_fast.py backend\requirements.txt "%DEPS_STAMP%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo Installing backend dependencies once...
    "%PY%" -m pip install -r backend\requirements.txt >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Dependencies failed to install. See %LOG%
        type "%LOG%"
        pause
        exit /b 1
    )
    "%PY%" tools\diagnostics\check_deps_fast.py backend\requirements.txt "%DEPS_STAMP%" --write >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Dependency import check failed. See %LOG%
        type "%LOG%"
        pause
        exit /b 1
    )
)

REM ---- Docker Desktop ----
echo [3/8] Checking Docker Desktop...
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not installed or docker.exe is not in PATH.
    echo Install Docker Desktop, start it, then run commands\launch\START_WEB_LOCAL.bat again.
    pause
    exit /b 1
)
docker compose version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose plugin is unavailable.
    pause
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        echo Starting Docker Desktop...
        start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    )
    "%PY%" tools\diagnostics\wait_for_docker.py --timeout 150 >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Docker Desktop did not start. Open it manually and try again.
        type "%LOG%"
        pause
        exit /b 1
    )
)

REM ---- Local PostgreSQL config ----
echo [4/8] Preparing local PostgreSQL...
"%PY%" tools\diagnostics\prepare_local_web_env.py "%WEB_ENV%" --port 54329 >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not prepare local database configuration.
    type "%LOG%"
    pause
    exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%WEB_ENV%") do set "%%A=%%B"
set "HIGHLIGHT_STUDIO_DATABASE_URL=postgresql+psycopg://!POSTGRES_USER!:!POSTGRES_PASSWORD!@127.0.0.1:!POSTGRES_PORT!/!POSTGRES_DB!"

docker compose -p highlight-studio-local-web --env-file "%WEB_ENV%" -f deploy\docker-compose.local-web.yml up -d >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] PostgreSQL container failed to start.
    type "%LOG%"
    pause
    exit /b 1
)

REM ---- Database readiness and migrations ----
echo [5/8] Waiting for PostgreSQL...
"%PY%" tools\diagnostics\wait_for_postgres.py --url "!HIGHLIGHT_STUDIO_DATABASE_URL!" --timeout 120 >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] PostgreSQL did not become ready.
    type "%LOG%"
    pause
    exit /b 1
)

set "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE=web"
set "HIGHLIGHT_STUDIO_AUTO_CREATE_DATABASE=0"
set "HIGHLIGHT_STUDIO_COOKIE_SECURE=0"
set "HIGHLIGHT_STUDIO_ALLOW_REGISTRATION=1"
set "HIGHLIGHT_STUDIO_REQUIRE_EMAIL_VERIFICATION=0"
set "HIGHLIGHT_STUDIO_DEV_AUTH_TOKENS=1"
set "HIGHLIGHT_STUDIO_SESSION_DAYS=30"

REM ---- Alembic migrations ----
echo [6/8] Applying database migrations...
"%PY%" -m alembic -c alembic.ini upgrade head >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Database migration failed.
    type "%LOG%"
    pause
    exit /b 1
)

REM ---- Find a free local app port ----
echo [7/8] Selecting local web port...
set "WEB_PORT_FILE=.highlight_studio\web_port.txt"
"%PY%" tools\diagnostics\find_free_port.py 8010 8099 > "!WEB_PORT_FILE!"
if errorlevel 1 (
    echo [ERROR] No free port between 8010 and 8099.
    pause
    exit /b 1
)
set /p WEB_PORT=<"!WEB_PORT_FILE!"
if not defined WEB_PORT (
    echo [ERROR] No free port between 8010 and 8099.
    pause
    exit /b 1
)
set "HIGHLIGHT_STUDIO_PUBLIC_BASE_URL=http://127.0.0.1:!WEB_PORT!"
set "WEB_URL=http://127.0.0.1:!WEB_PORT!"

REM ---- Backend import and startup ----
echo [8/8] Starting multi-user web mode...
"%PY%" -c "import backend.src.highlight_studio.api.app; print('Web backend import OK')" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Web backend failed to import.
    type "%LOG%"
    pause
    exit /b 1
)

echo.
echo ======================================================
echo Local Web is starting: !WEB_URL!
echo First registered account becomes administrator.
echo PostgreSQL data is preserved between launches.
echo Stop the database later with commands\launch\STOP_WEB_LOCAL.bat.
echo ======================================================
echo.
start "" /B "%PY%" tools\diagnostics\wait_and_open.py !WEB_URL! / "v11.2.7-quality-recovery-audit" "studio-audited-v15"
"%PY%" -m uvicorn backend.src.highlight_studio.api.app:app --host 127.0.0.1 --port !WEB_PORT! --no-access-log

echo.
echo Web server stopped. PostgreSQL is still running for faster next launch.
echo Use commands\launch\STOP_WEB_LOCAL.bat if you want to stop the database container.
echo Log: %CD%\%LOG%
pause
