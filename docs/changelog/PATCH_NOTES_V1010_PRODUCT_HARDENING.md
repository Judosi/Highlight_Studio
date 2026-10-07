# v10.1.0 — Product Hardening / 9 из 10 candidate

Эта версия закрывает критичные проблемы, найденные при глубоком аудите v10.0.5.

## Безопасность

- Закрыт path traversal через `project_id`: теперь ID проекта строго валидируется и путь обязан оставаться внутри `projects/`.
- Локальный токен больше не возвращается в JSON `/api/health` и не подставляется в URL файлов/видео.
- `/api/health` выставляет HttpOnly cookie `highlight_studio_local_token`.
- Query-token auth отключён по умолчанию. Для старой отладки можно включить `HIGHLIGHT_STUDIO_ALLOW_QUERY_TOKEN=1`.
- Twitch URL теперь принимает только настоящий `twitch.tv` или поддомены `*.twitch.tv`; домены вида `twitch.tv.evil.example` отклоняются.

## Экспорт и релиз

- `/api/projects/{id}/export` теперь делает безопасный metadata-export.
- Из экспорта исключаются исходные видео, WAV/chunks, render parts, outputs, preview/HLS, heavy binaries, `jobs.sqlite3` и `local_auth_token.txt`.
- Добавлен release packager `tools/make_release.py`, который собирает zip по allowlist и не кладёт runtime-состояние пользователя.

## Кэширование и производительность

- Транскрипт теперь имеет `transcript_manifest.json` с fingerprint исходника и Whisper-настроек.
- Если изменился исходник, язык, модель Whisper, compute/device или chunk size — транскрипт пересчитывается, а старые chunks очищаются.
- После успешной транскрибации удаляется большой временный `audio_16k.wav`.
- Scene detection получил fingerprint-кэш через `scene_times_manifest.json`.

## Рендер и качество

- `remove_silence` больше не применяется одновременно с SRT-субтитрами, чтобы не ломать тайминги.
- Аппаратные encoder'ы больше не являются фиктивной настройкой: приложение проверяет доступность FFmpeg encoder и использует его, если он реально есть; иначе пишет понятный fallback в `libx264`.
- Усилен `result_check`: теперь проверяются длительность, видеопоток, размер и аудиопотоки.
- Добавлен `strict_quality_mode`: можно выбрать честный режим «только сильные моменты», даже если итог получится короче целевой длительности.
- Добавляется `selection_report.json` с фактической длительностью и режимом выбора.

## Проверки

- Python compile: OK.
- Backend tests: 59/59 passed.
- Frontend production build: OK.
