# Highlight Studio v9.6.0 — One-click Stable Product

Цель релиза: объединить аудит UX/Product/Tech в один понятный рабочий проект без удаления старых функций.

## Главное

- Добавлен главный сценарий **«Собрать нарезку»**: one-click job подготавливает Twitch-источник, запускает AI-анализ через Ollama и собирает финальный список моментов.
- Добавлен **Ollama Monitor**: показывает статус локального AI, активные модели через `ollama ps` и даёт кнопку выгрузки моделей.
- Добавлен блок **«Восстановление и продолжение»**: показывает checkpoint’ы проекта — источник, транскрипт, кандидаты, финальный список, metadata, render.
- Улучшен Review Studio: отдельная карточка объясняет, что это главный экран контроля качества перед рендером.
- Улучшен Export Center: добавлен Creator Pack summary.
- Сохранены все функции прошлых версий: Twitch Import, Fast Import, IRL, OCR, Visual scan, AI Strict, Metadata, Render, Shorts, Export, Reports, Compare mode, Project Manager, hardware presets.

## Backend

- `GET /api/ollama/monitor` — лёгкая диагностика Ollama и `ollama ps`.
- `GET /api/projects/{project_id}/recovery` — состояние checkpoint’ов и следующий рекомендуемый шаг.
- `POST /api/projects/{project_id}/one-click` — стабильный one-click pipeline: prepare Twitch source if needed → analyze → pre-render check.

## Проверка

- `python -m compileall backend tests tools` — OK
- `pytest -q` — 31 passed
- `npm --prefix frontend run build` — OK
- `/api/health` — OK
- `/api/system-check` — OK
- `/` frontend — OK
