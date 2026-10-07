# v9.9.7 Safety Guard

Главная цель версии: сделать проект безопаснее для долгих 3–6 часовых задач, чтобы случайное закрытие приложения, сбой Windows или прерывание записи JSON не ломали проект.

## Что добавлено

- Atomic JSON writes: важные JSON-файлы теперь пишутся через временный файл и `os.replace`, чтобы не оставлять половину файла при сбое.
- Backup `.bak` для критичных файлов проекта: `project.json`, `status.json`, `segments.json`, `candidates.json`, `twitch_import.json`, `cache_manifest.json`, `pre_render_check.json`, `youtube_metadata.json`.
- Safe JSON read: если основной JSON повреждён, приложение автоматически читает валидный `.bak`.
- Project Integrity API:
  - `GET /api/projects/{project_id}/integrity`
  - `POST /api/projects/{project_id}/repair-json-backups`
- Dashboard-state теперь отдаёт `integrity`, чтобы UI видел состояние сохранности проекта.
- В разделе “Отчёты” добавлен блок Safety Guard: показывает повреждённые JSON, восстановимые backup и состояние исходного видео.
- Кнопка восстановления из backup для повреждённых JSON-файлов.

## Что не менялось

- Twitch Turbo Downloader, TDCLI/aria2/yt-dlp fallback.
- Ollama, Whisper, OCR, Visual Scan.
- Import, Review Studio, Render, YouTube/Shorts.
- Существующая логика pipeline.

## Проверка

- `pytest -q` → 41 passed
- `npm --prefix frontend run build` → успешно
- `npm --prefix frontend audit --audit-level=moderate` → 0 vulnerabilities
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` → успешно
