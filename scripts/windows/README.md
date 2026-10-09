# Windows launch scripts

## Готовая portable-сборка

Ручной GitHub Actions workflow `Build ready Windows portable ZIP` создаёт автономный unsigned test ZIP (или подписанный ZIP при наличии сертификата), проверяет распакованный EXE на Windows и публикует его как Actions Artifact. GitHub Source Code ZIP не заменяет этот артефакт.

Release pipeline использует `build_hybrid_release.ps1`, затем проверяет packaged layout, создаёт ZIP через `package_portable_release.ps1` и запускает `test_portable_release.ps1` из пути с пробелами и кириллицей. FFmpeg и TwitchDownloaderCLI загружаются только из закреплённых HTTPS-источников с обязательным SHA-256.

## Source/developer launchers

Use the root launchers:

- `START_HERE.bat` — mode selection menu;
- `commands\launch\START_DESKTOP.bat` — local single-user desktop mode;
- `commands\launch\START_WEB_LOCAL.bat` — local multi-user web mode with PostgreSQL in Docker;
- `commands\launch\STOP_WEB_LOCAL.bat` — stop the local PostgreSQL container without deleting data.

The scripts in this directory are implementation details used by the root launchers. Normal users should not need to run them directly.

`commands\launch\START_WEB_LOCAL.bat` requires Docker Desktop. It binds PostgreSQL only to `127.0.0.1`, applies Alembic migrations and opens the registration page on a free port from 8010 to 8099.
