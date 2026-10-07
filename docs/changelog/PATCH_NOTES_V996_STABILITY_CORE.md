# Highlight Studio v9.9.6 — Stability Core

Эта версия не раздувает проект новыми кнопками, а доводит основу до более надёжного состояния для реальной работы с длинными Twitch VOD.

## Главное

1. **Единый dashboard-state endpoint**
   - Раньше frontend каждые 2 секунды дергал 20+ endpoints: status, logs, candidates, quality, metadata, pre-render, cache, checkpoints и т.д.
   - Теперь основной UI получает состояние проекта через один endpoint:
     - `GET /api/projects/{project_id}/dashboard-state`
   - Это уменьшает шум в консоли, снижает мелкую нагрузку на backend и делает обновления интерфейса стабильнее.

2. **Умный polling**
   - Во время активной задачи UI обновляется быстро: примерно каждые 2.5 секунды.
   - Когда проект ничего не делает, UI обновляется реже: примерно каждые 12 секунд.
   - Лишние запросы не мешают Whisper/Ollama/TDCLI на слабом ПК.

3. **Тихий запуск backend**
   - `run_windows.bat` и `start_backend.bat` теперь запускают Uvicorn с `--no-access-log`.
   - Консоль больше не забивается десятками строк `GET ... 200 OK`.
   - Для диагностики добавлен `start_backend_debug.bat`, который включает access logs обратно.

4. **Кэшированный Ollama monitor**
   - `ollama ps` больше не дергается слишком часто.
   - Статус Ollama кэшируется на несколько секунд, чтобы не грузить систему лишними командами.

5. **Надёжное JSON-состояние**
   - `dashboard-state` собирает UI-данные безопасно: если один маленький отчёт не прочитался, весь dashboard не падает.
   - В ответ добавляется список `warnings`, а рабочие блоки продолжают отображаться.

## Что сохранено

- Twitch Turbo Downloader: TDCLI, aria2c, yt-dlp fallback, speed-test.
- Fast Import, Twitch VOD/Live, обычная загрузка.
- Ollama, Whisper, OCR, Visual Scan.
- Task-пресеты, hardware-пресеты, Autopilot.
- Resume/checkpoints, cache fingerprint, preflight.
- Review Studio, Render, YouTube/Shorts helper.
- Старый backend API оставлен для совместимости.

## Проверка

- `pytest -q` — 39 tests passed.
- `npm --prefix frontend run build` — successful.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py` — successful.
