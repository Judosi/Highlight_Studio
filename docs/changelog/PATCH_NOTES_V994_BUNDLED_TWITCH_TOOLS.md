# Highlight Studio v9.9.4 — Bundled Twitch Turbo Tools

Эта версия сделана для случая, когда в приложении нельзя удобно указать путь к `TwitchDownloaderCLI.exe` и `aria2c.exe`.

## Что изменено

- В проект добавлены встроенные downloader-инструменты:
  - `external_tools/twitchdownloadercli/TwitchDownloaderCLI.exe`
  - `external_tools/aria2/aria2c.exe`
- Backend автоматически добавляет эти папки в `PATH` при запуске на Windows.
- `run_windows.bat` и `start_backend.bat` тоже добавляют встроенные инструменты в `PATH`.
- В настройках больше не нужно вручную указывать путь к TwitchDownloaderCLI.
- Экран Twitch Turbo показывает `OK · встроен`, если инструмент найден внутри проекта.
- Auto Turbo теперь может использовать встроенный TwitchDownloaderCLI и встроенный aria2c.

## Как пользоваться

1. Распакуй архив.
2. Запусти `START_HERE.bat` или `run_windows.bat`.
3. Открой вкладку `Импорт` → `Twitch VOD`.
4. Нажми `Проверить движки`.
5. Должно быть:
   - `TDCLI: OK · встроен`
   - `yt-dlp: OK`
   - `aria2c: OK · встроен`
6. Нажми `Тест скорости`.
7. Оставь движок `Auto Turbo` и запускай подготовку VOD.

## Важно

`TwitchDownloaderCLI.exe` и `aria2c.exe` — это Windows-инструменты. В Linux/macOS они не будут запускаться как нативные бинарники.
