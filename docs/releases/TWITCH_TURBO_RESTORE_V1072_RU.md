# Highlight Studio v10.7.2 — Twitch Turbo Restore

Исправлена регрессия скорости Twitch VOD между v10.0.5 и v10.7.1.

- Source/ZIP launcher снова работает в portable-режиме и хранит cache рядом с программой.
- Простой режим всегда использует Auto Turbo, а не скрытое сохранённое значение yt-dlp.
- Auto Turbo предпочитает встроенный TwitchDownloaderCLI, затем aria2, затем yt-dlp.
- Добавлена диагностика медленных синхронизируемых и сетевых папок.
- В лог пишутся фактически выбранный движок и путь Twitch cache.
