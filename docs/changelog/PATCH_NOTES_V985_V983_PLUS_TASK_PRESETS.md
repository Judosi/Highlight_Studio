# Highlight Studio v9.8.5 — добавлено поверх v9.8.3 Product Audit Fix

Эта версия собрана именно на базе `highlight_studio_v983_product_audit_fix`.
Старая функциональность v9.8.3 сохранена: Fast Import, Twitch VOD/Live, Project Manager, Ollama Local, Whisper, Visual Scan, OCR, Review Studio, рендер, metadata, отчёты и защита готовности источника.

## Что добавлено

### 1. Один главный режим «Собрать нарезку»
- В интерфейсе появился главный сценарий, который ведёт пользователя по шагам.
- Кнопка «Собрать нарезку» запускает понятный one-click flow поверх существующих backend-команд.
- Старые технические кнопки не удалены: они остались в Pro/дополнительных блоках.

### 2. Task-пресеты
Добавлены рабочие backend + frontend пресеты:
- `irl_funny` — IRL смешное;
- `irl_conflict` — IRL конфликт / хаос;
- `sport` — Спорт / теннис;
- `podcast` — Подкаст / разговор;
- `shorts_only` — Shorts only.

Пресеты реально сохраняются в `project.json` через endpoint:
- `GET /api/task-presets`
- `POST /api/projects/{project_id}/task-preset`

Они меняют `content_type`, `edit_mode`, `target_minutes`, `micro_cut`, `Visual/OCR`, `hook_first`, `storyline` и prompt.

### 3. AI-объяснение моментов
Каждый кандидат теперь получает дополнительные поля:
- `what_happens` — что происходит;
- `why_selected` — почему AI выбрал фрагмент;
- `viewer_value` — почему это интересно зрителю;
- `ai_explanation` — объединённое объяснение.

Даже если старый кэш или модель вернула короткую причину, pipeline добавляет понятное объяснение автоматически.

### 4. Диагностика зависаний
В `/api/projects/{project_id}/status` добавлено поле `diagnosis`.
Интерфейс теперь может объяснять, что делать, если долго висит:
- Whisper;
- Ollama / AI batch;
- yt-dlp / Twitch download;
- общий pipeline.

## Проверки

- `pytest -q`: 34 теста пройдено.
- `npm --prefix frontend run build`: успешно.
- `npm --prefix frontend audit --audit-level=moderate`: 0 vulnerabilities.

## Важно

Реальный анализ видео не запускался в контейнере, потому что для него нужны локальные FFmpeg/Ollama/видео/Twitch-сеть. Но backend-тесты, frontend-сборка и npm audit прошли успешно.
