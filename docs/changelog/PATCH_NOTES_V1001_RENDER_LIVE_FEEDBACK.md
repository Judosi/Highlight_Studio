# v10.0.1 Render Live Feedback

Исправлено: рендер YouTube и Shorts могли выполняться правильно, но UI не показывал живой прогресс/лог.

Что добавлено:
- optimistic UI state сразу после нажатия Render/Shorts;
- быстрый polling на вкладке Export;
- status_history в dashboard-state;
- Live Render / Shorts panel во вкладке Export;
- последние этапы, проценты, ETA, часть/клип и tail логов;
- более честный прогресс FFmpeg: часть/short помечается как старт и готово;
- backend пишет явные сообщения старта Render/Shorts.

Старый pipeline не переписан: Twitch Turbo, Ollama, Whisper, OCR, Visual Scan, render и Shorts сохранены.
