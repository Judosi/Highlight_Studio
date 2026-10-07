# Windows launch scripts

Use the root launchers:

- `START_HERE.bat` — mode selection menu;
- `commands\launch\START_DESKTOP.bat` — local single-user desktop mode;
- `commands\launch\START_WEB_LOCAL.bat` — local multi-user web mode with PostgreSQL in Docker;
- `commands\launch\STOP_WEB_LOCAL.bat` — stop the local PostgreSQL container without deleting data.

The scripts in this directory are implementation details used by the root launchers. Normal users should not need to run them directly.

`commands\launch\START_WEB_LOCAL.bat` requires Docker Desktop. It binds PostgreSQL only to `127.0.0.1`, applies Alembic migrations and opens the registration page on a free port from 8010 to 8099.
