# PATCH NOTES V9.6.2 — Fast Twitch VOD Turbo

Добавлено ускоренное скачивание Twitch VOD прямо в приложении.

## Что изменено

- VOD теперь качается через `yt-dlp` с параллельными HLS/DASH-фрагментами: `-N / --concurrent-fragments`.
- В интерфейс Twitch Import добавлены настройки:
  - количество потоков загрузки: 1–64, по умолчанию 16;
  - cookies из браузера: None / Firefox / Chrome / Edge / Brave / Opera / Vivaldi;
  - format selector yt-dlp, по умолчанию `best`.
- Backend добавляет стабильные флаги для длинных VOD:
  - `--continue`;
  - `--retries infinite`;
  - `--fragment-retries infinite`;
  - `--file-access-retries 10`;
  - `--socket-timeout 30`;
  - `--download-sections` для выбранного диапазона VOD.
- Прогресс-бар теперь показывает не только размер Twitch cache и скорость, но и выбранные `-N` потоки и cookies browser.
- Перед новой попыткой импорта очищаются только временные `.part/.ytdl/.tmp` файлы, готовый VOD cache не удаляется.
- Ошибка cookies на Windows стала понятнее: приложение подскажет закрыть браузер или выбрать другой режим cookies.

## Рекомендация

Для хорошего интернета начинай с `16` потоков. Если Twitch/сеть не сыпет ошибки — можно попробовать `32`. Если появляются ошибки или замедления — верни `16`.
