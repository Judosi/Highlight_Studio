# v9.2.2 — Start Fix / Vite Auto Install

Исправлена проблема запуска frontend на Windows: `vite` не найден или сломался `node_modules` после распаковки архива.

Что изменено:
- `run_windows.bat` теперь запускается из своей папки через `cd /d "%~dp0"`.
- frontend-зависимости проверяются не только по папке `node_modules`, но и по `node_modules\.bin\vite.cmd`.
- если Vite не найден или не запускается, скрипт автоматически переустанавливает frontend-зависимости.
- frontend запускается через `npx vite`, что надёжнее для локального Windows-проекта.
- `start_frontend.bat` и `setup_windows.bat` тоже стали безопаснее.
- `node_modules` больше не упаковывается в архив, чтобы не переносить сломанные Linux/Windows-ссылки.

Все функции v9.2.1 сохранены.
