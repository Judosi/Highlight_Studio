# Highlight Studio v10.0.3 — Release Hardening

Цель версии: не менять рабочее AI-ядро, которое уже хорошо находит моменты, а закрыть реальные продуктовые риски вокруг стабильности, упаковки, понятности сценария и контроля результата.

## Плюсы проекта, которые сохранены

- Twitch Turbo Downloader со встроенными TwitchDownloaderCLI/aria2/yt-dlp fallback.
- Локальный AI через Ollama: qwen3:8b + qwen3-vl:8b.
- Whisper/OCR/Visual Scan, task-пресеты и hardware-пресеты.
- Review Studio, timeline, micro-player, render, Shorts и Creator Pack.
- Safety Guard, Product Doctor, Quality Doctor и Stable Candidate.

## Исправленные минусы

### 1. Убрано FastAPI deprecation warning
`@app.on_event("startup")` заменён на современный `lifespan`, поэтому тесты больше не дают предупреждений.

### 2. Release Audit
Добавлен endpoint:

`GET /api/app-audit`

Он проверяет:
- наличие production frontend build;
- START_HERE/run/start_backend scripts;
- встроенные TDCLI/aria2;
- количество pytest smoke tests;
- размер App.jsx/main.py/pipeline.py как зоны будущего рефакторинга;
- README/STABLE docs.

### 3. Workflow Guard
Добавлен endpoint:

`GET /api/projects/{project_id}/workflow-guard`

Он показывает, что делать дальше:
- подготовить источник;
- запустить анализ;
- перейти в монтаж;
- выполнить рендер;
- открыть готовый результат.

Главная карточка теперь использует `workflow_guard.next_action`, поэтому интерфейс лучше ведёт пользователя.

### 4. Render Artifact Check
Добавлен endpoint:

`GET /api/projects/{project_id}/render-artifact-check`

Он проверяет наличие финального `highlight_final.mp4`, Shorts и outputs после рендера.

### 5. UI Reports усилен
Во вкладку «Отчёты» добавлены:
- Workflow Guard;
- Render Artifact Check;
- Release Audit.

### 6. Меньше блокирующих alert
Убраны `alert()` из основных сценариев. Теперь сообщения идут через красивый верхний notice/error banner и не блокируют приложение.

### 7. Сохранение сегментов безопаснее
Если сохранение финального монтажа не удалось, UI теперь показывает понятную ошибку вместо молчаливого продолжения.

### 8. Polling теперь уважает backend
Frontend берёт `poll_after_ms` из `dashboard-state`, чтобы не опрашивать backend чаще, чем нужно.

### 9. Мой рабочий пресет можно применить без проекта
Если проект ещё не создан, рабочий пресет применится к локальному профилю и автоматически попадёт в новый проект.

## Проверки

- `pytest -q` — 53 passed.
- `npm --prefix frontend run build` — успешно.
- `npm --prefix frontend audit --audit-level=moderate` — 0 vulnerabilities.
- `python -m py_compile backend/main.py backend/pipeline.py backend/settings.py backend/twitch_source.py backend/utils.py` — успешно.

Реальный длинный VOD в контейнере не запускался: для этого нужны локальные Windows, FFmpeg, Ollama, модели и Twitch-ссылка.
