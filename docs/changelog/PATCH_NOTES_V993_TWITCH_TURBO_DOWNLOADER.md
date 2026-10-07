# v9.9.3 — Twitch Turbo Downloader

Эта версия сделана поверх v9.9.2 Ready Product UI. Старые функции сохранены, но Twitch VOD import получил новый быстрый слой загрузки.

## Главное

- Добавлен `Twitch Turbo Downloader`.
- Добавлен выбор движка загрузки Twitch:
  - `Auto Turbo`
  - `TwitchDownloaderCLI`
  - `yt-dlp + aria2c`
  - `yt-dlp Turbo`
  - `TwitchLink manual import`
- Добавлен speed-test Twitch VOD: приложение пробует короткий диапазон и выбирает самый быстрый движок.
- Добавлен fallback: если основной движок не сработал, приложение пробует резервные варианты.
- Добавлены настройки качества, потоков, aria2 connections, cookies, fallback и путь к TwitchDownloaderCLI.exe.
- Добавлены backend endpoints:
  - `GET /api/twitch/tools`
  - `POST /api/twitch/plan`
  - `GET /api/projects/{project_id}/twitch-download-plan`
  - `POST /api/projects/{project_id}/twitch-speed-test`
- Smart Preflight теперь учитывает не только yt-dlp, но и TwitchDownloaderCLI как полноценный Twitch downloader.

## Почему это важно

Если TwitchLink скачивает VOD сильно быстрее, чем текущий yt-dlp, вероятная причина — другой способ параллельного скачивания HLS-сегментов. Поэтому теперь проект не зависит от одного способа. Рекомендуемый путь: установить TwitchDownloaderCLI.exe и оставить engine `Auto Turbo`.

## Как использовать

1. Открой `Импорт → Twitch VOD`.
2. Вставь ссылку на VOD.
3. Выбери engine `Auto Turbo`.
4. Нажми `Тест скорости`.
5. Если найден быстрый движок, он будет выбран автоматически.
6. Нажми `Создать и подготовить`.

Если ничего не работает — скачай VOD через TwitchLink и импортируй готовый mp4 как локальный файл. Анализ/нарезка продолжат работать как обычно.

## Проверки

- `npm --prefix frontend run build` — успешно.
- `pytest -q` — 37 тестов пройдено.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/twitch_source.py backend/pipeline.py backend/settings.py` — успешно.
