# Patch notes v8.7.0-ready

Готовый проект после правок P0/P1/P2.

## Проверено

- `python -m compileall backend tests`
- `pytest -q` → 5 passed
- `npm --prefix frontend install`
- `npm --prefix frontend run build`

`frontend/node_modules` не входит в архив, чтобы ZIP оставался лёгким. При установке запусти `setup_windows.bat` или `npm --prefix frontend install`.

## Основные изменения

- Pydantic validation для backend settings.
- Local-only CORS.
- Read-only GET для отчётов, POST для пересчёта.
- Отдельный `cancelled` статус.
- Adaptive Auto UI и backend.
- Quality Core UI.
- Глобальный лимит тяжёлых jobs.
- Project Manager: delete / clear cache / export zip / project folder path.
- `app_version` и `settings_version` в `project.json`.
- Smoke tests вместо старых `TEST_REPORT*.json`.
- Overlap resolver после Storyline.
- Confidence score для кандидатов/сегментов.
- A/B режимы монтажа.
- Shorts vertical reframe 9:16.
- Rough-cut preview.
