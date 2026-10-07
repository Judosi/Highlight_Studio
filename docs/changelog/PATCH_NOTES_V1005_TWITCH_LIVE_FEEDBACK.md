# v10.0.5 — Twitch Live Feedback

Исправлена отдельная проблема Twitch VOD import: скачивание фактически шло, но UI мог не показывать живую полоску прогресса и логи, потому что downloader запускался через `communicate()` и его вывод попадал в backend только после завершения.

## Изменения

- Twitch VOD downloader теперь запускается через streaming runner.
- Вывод TDCLI / yt-dlp пишется в `logs.txt` во время скачивания.
- `status.json` обновляется во время скачивания: progress, stage progress, cache MB, speed MB/s, engine, последняя строка downloader-а.
- Вкладка **Импорт** получила отдельную карточку **Live Twitch Download**.
- Даже если downloader молчит, UI получает heartbeat каждые ~2 секунды.

## Безопасность

AI-анализ, рендер, Review Studio, Twitch Turbo fallback и настройки не переписывались. Изменение касается только live feedback для Twitch import.
