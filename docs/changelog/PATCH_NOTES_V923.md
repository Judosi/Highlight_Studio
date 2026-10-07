# v9.2.3 — Dependency Fix

Исправлен запуск frontend на Windows.

- run_windows.bat теперь проверяет не только Vite, но и react/react-dom/lucide-react.
- Если node_modules неполный или сломан, зависимости переустанавливаются автоматически.
- Frontend запускается через локальный `.\node_modules\.bin\vite.cmd`, а не через случайный npx-кэш.
- Добавлен более понятный setup_windows.bat.
