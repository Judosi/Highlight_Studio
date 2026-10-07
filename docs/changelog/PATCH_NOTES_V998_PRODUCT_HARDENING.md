# v9.9.8 Product Hardening

Цель версии: закрыть самые важные минусы проекта без перелома старой логики и без добавления рискованных функций.

## Что добавлено

### 1. Product Health Center
Новый отчёт готовности проекта к реальной работе с длинным VOD.

Проверяет:
- исходное видео реально готово;
- Safety Guard / JSON integrity;
- Smart Preflight;
- совместимость cache fingerprint;
- checkpoints / resume;
- наличие AI-кандидатов;
- наличие финальных фрагментов;
- близость итоговой длительности к цели;
- качество перед рендером;
- создан ли итоговый файл;
- безопасны ли настройки для слабого ПК.

Backend:
- `GET /api/projects/{project_id}/product-readiness`

Frontend:
- Отчёты → Product Health Center.

### 2. Debug Bundle
Добавлен безопасный экспорт диагностического архива без видео и больших cache/render файлов.

Backend:
- `POST /api/projects/{project_id}/debug-bundle`

В bundle входят:
- product readiness report;
- integrity report;
- preflight;
- checkpoints;
- cache report;
- project/status/segments/candidates JSON, если они небольшие;
- хвост logs.txt.

Видео, итоговые mp4 и тяжёлый cache не включаются.

### 3. Исправлен скрытый баг Reports
В предыдущей версии dashboard-state отдавал `integrity`, но React не сохранял его в state при обычном обновлении.
Теперь `setIntegrity()` вызывается внутри `applyDashboardState()`, отчёт Safety Guard обновляется корректно.

### 4. Product Health добавлен в dashboard-state
`dashboard-state` теперь возвращает `product_readiness`, поэтому интерфейс получает health-report без лишних отдельных запросов.

## Проверка

- `pytest -q` — 43 passed
- `npm --prefix frontend run build` — OK
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` — OK

## Что не трогалось

Старые функции сохранены:
- Twitch Turbo Downloader;
- TDCLI / aria2 / yt-dlp fallback;
- Fast Import;
- Twitch VOD / Live;
- Ollama;
- Whisper;
- OCR;
- Visual Scan;
- Review Studio;
- Render;
- YouTube/Shorts;
- Stability Core;
- Safety Guard.
