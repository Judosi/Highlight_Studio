# v9.9.5 — Twitch speed-test JSON-safe fix

Исправлена ошибка UI: `JSON.parse: unexpected character at line 1 column 1`, которая появлялась, если backend speed-test возвращал не JSON (например plain text/HTML 500 от внешнего downloader или сервера).

## Изменения
- Frontend speed-test теперь читает ответ через безопасный `safeJsonResponse`, а не падает на `r.json()`.
- Backend получил глобальный JSON exception handler: даже неожиданные ошибки API возвращаются как JSON.
- `responseError()` больше не ломает body response двойным чтением.
- Twitch Tools refresh также устойчив к non-JSON ответу.

## Что делать пользователю
1. Распаковать проект.
2. Запустить `START_HERE.bat`.
3. Нажать `Проверить движки`.
4. Нажать `Тест скорости`.

Если TDCLI/aria2/yt-dlp не смогут скачать sample, приложение покажет понятную причину, а не JSON.parse.
