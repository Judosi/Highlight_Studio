# v9.2.5 Windows Launcher Fix

- Reworked `run_windows.bat` to run backend and UI in the same terminal window.
- Removed fragile `start cmd /k ...` startup path that could hide errors.
- Added automatic `.venv` creation and backend dependency install.
- Added startup log at `.highlight_studio/logs/startup.log`.
- Normal launch still uses prebuilt `frontend/dist`; Vite/Node are not required.
- Added clear failure messages for missing Python, broken venv, missing frontend dist, dependency install errors and backend import errors.
